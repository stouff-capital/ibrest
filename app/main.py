#!/usr/bin/python
""" IBREST API Flask app controller file.  This file establishes the routes used for the IBREST API.  More info at:
https://github.com/hamx0r/IBREST

Most of this API takes HTTP requests and translates them to EClientSocket Methods:
https://www.interactivebrokers.com/en/software/api/apiguide/java/java_eclientsocket_methods.htm

The HTTP response is handled by compiling messages from EWrappper Methods into JSON:
https://www.interactivebrokers.com/en/software/api/apiguide/java/java_ewrapper_methods.htm
"""

# DO THIS FIRST!  Logging import and setup
import logging

log_format = '%(asctime)s %(levelname)-5.5s [%(name)s-%(funcName)s:%(lineno)d][%(threadName)s] %(message)s'
logging.basicConfig(format=log_format, level=logging.DEBUG)
# Flask imports
from flask import Flask, request
from flask_restful import Resource, Api, reqparse, abort
# IBREST imports
import sync, feeds
import parsers
import globals as g
import utils
import json
import time
import os
import connection
# Beacon imports
import requests
from datetime import datetime
from functools import wraps
from ib.opt import ibConnection
# database setup
from database import init_db, FilledOrders, Commissions
init_db()

__author__ = 'Jason Haury'

app = Flask(__name__)
api = Api(app)

# Logger for this module
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------
def authenticate(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not getattr(func, 'authenticated', True) or g.serializer is None:
            return func(*args, **kwargs)

        # authorized is true when the token from the decrypted Beacon-Token matches either our current or last token
        # log.debug('Auth looking for flare in {}'.format(request))
        flare = request.headers.get('Beacon-Flare', None)
        # log.debug('Auth  flare: {}'.format(flare))
        if flare is None:
            log.warn('No Beacon-Flare in header')
            abort(401)

        # TODO remove this hack when not developing
        if flare == '1p2o3i4u5y':
            log.warn('Using vulnerable Beacon-Flare')
            return func(*args, **kwargs)

        ibrest_info = g.serializer.loads(flare)
        authorized = False
        # if ibrest_info['ip'] == g.current_ip:
        if ibrest_info['token'] in [g.beacon_current_token, g.beacon_last_token]:
            authorized = True

        log.debug('Authorized = {}'.format(authorized))
        if authorized:
            return func(*args, **kwargs)

        abort(401)

    return wrapper


def send_flare_to_gae():
    token = datetime.now().strftime('%D_%H:%M')
    # we want to save our last token in case we get a bit out of sync - both can be valid for a while
    if token == g.beacon_current_token:
        # Nothing's changed since the last time this endpoint was called, so return now
        return
    else:
        # Things have changed, so update our last and current tokens
        g.beacon_last_token = g.beacon_current_token
        g.beacon_current_token = token

    # Since our token has been updated, we need to tell GAE about this
    # First get our secret key from env vars
    secret_key = g.id_secret_key
    if secret_key is None:
        log.error('No secret key')
        return None, 500

    # We want to tell GAE our IP address too
    # curl -H "Metadata-Flavor: Google" http://metadata/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip
    # headers = {'Metadata-Flavor': 'Google'}
    # metadata_url = "http://metadata/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip"
    # ip = requests.get(metadata_url, headers=headers).text
    # if ip is not None:
    #     g.current_ip = ip

    # set up a response for be encrypted & signed
    # resp = {'ip': ip, 'token': token}
    resp = {'token': token}
    # Now sign it, seal it...:
    flare = g.serializer.dumps(resp)
    resp['payload'] = flare

    # ...and deliver it:
    put_resp = requests.put('https://orion-minute.appspot.com/beacon', data={'flare': flare}).text
    resp['put_resp'] = put_resp
    log.debug('Beacon: {}'.format(resp))

    return resp


# ---------------------------------------------------------------------
# RESOURCES
# ---------------------------------------------------------------------
class History(Resource):
    """ Resource to handle requests for historical data (15min delayed)
    """

    method_decorators = [authenticate]

    def get(self, symbol):
        """ Uses reqHistoricalData() to start a stream of historical data, then upon getting data in that streatm,
        cancels the stream with cancelHistoricalData() before returning the history
        """
        return utils.make_response(feeds.get_history(symbol, request.args))


class Market(Resource):
    """ Resource to handle requests for market data
    """

    method_decorators = [authenticate]

    def get(self, symbol):
        """
        :return: JSON dict of dicts, with main keys being tickPrice, tickSize and optionComputation.
        """
        # TODO add query string params for Contract, and create feed accordingly
        return utils.make_response(feeds.get_market_data(symbol, request.args))


class Order(Resource):
    """ Resource to handle requests for Order
    """
    method_decorators = [authenticate]

    def get(self):
        """ Retrieves details of open orders using reqAllOpenOrders()
        """
        return utils.make_response(sync.get_open_orders())

    def post(self):
        """ Places an order with placeOrder().  This requires enough args to create a Contract & an Order:
        https://www.interactivebrokers.com/en/software/api/apiguide/java/java_socketclient_properties.htm

        To allow bracketed, a JSON list may be posted in the body with each list object being an order.  Arg
        parsing does not happen in this case
        http://interactivebrokers.github.io/tws-api/bracket_order.html

        Note: This implies the JSON list starts with an order to open a position followed by 1-2 orders for closing
                that position (profit taker, loss stopper)

        Option orders with Combo Legs can also be made with an order dict in a JSON list with secType = 'BAG'
        """
        # Detect a JSON object being posted
        # Convert to not-unicode
        all_args = request.json
        all_args = json.dumps(all_args)
        all_args = json.loads(all_args, object_hook=utils.json_object_hook)
        log.debug('all_args: {}'.format(all_args))
        # If there was no JSON object, then use query string params
        if all_args is None:
            parser = parsers.order_parser.copy()
            for arg in parsers.contract_parser.args:
                parser.add_argument(arg)
            args = parser.parse_args()

            all_args = {k: v for k, v in request.values.iteritems()}
            # update with validated data
            for k, v in args.iteritems():
                all_args[k] = v

        return utils.make_response(sync.place_order(all_args))

    def delete(self):
        """ Cancels order with cancelOrder()
        """
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument('orderId', type=int, required=True,
                            help='Order ID to cancel')
        args = parser.parse_args()
        return utils.make_response(sync.cancel_order(args['orderId']))


class OrderFilled(Resource):
    """ Resource to get filled orders.
    """
    method_decorators = [authenticate]


    def get(self):
        """ Retrieves details of filled orders using stored data in SQLite DB
        """
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument('orderId', type=int, required=False,
                            help='Order ID to get ExecutionReport for')
        args = parser.parse_args()
        orderId = args.get('orderId')

        if orderId is None:
            # Get all filled orders available
            resp = FilledOrders.query.all()
        else:
            resp = FilledOrders.query.filter(FilledOrders.order_id == orderId).first()
        resp = [r.order_status for r in resp]
        return utils.make_response(resp)



class OrderOCA(Resource):
    """ Resource to handle requests for Bracket-like OCA Orders

    Takes a JSON list of Orders.   Item 0 is always considered to be the opening order of a position, and the rest are
    the OCA group to close the position.

    This behaves like an elaborate Bracketed Order, but the logic is handled by IBREST instead of the IB GW client since
    this OCA groups are meant to work on a preexisting position.
    """
    method_decorators = [authenticate]

    def post(self):
        # Detect a JSON object being posted
        # Convert to not-unicode
        all_args = request.json
        all_args = json.dumps(all_args)
        all_args = json.loads(all_args, object_hook=utils.json_object_hook)
        return utils.make_response(sync.place_order_oca(all_args))


class PortfolioPositions(Resource):
    """ Resource to handle requests for market data
    """
    method_decorators = [authenticate]

    def get(self):
        """
        :return: JSON dict of dicts, with main keys being tickPrice, tickSize and optionComputation.
        """
        return utils.make_response(sync.get_portfolio_positions())


class AccountSummary(Resource):
    """ Resource to handle requests for account summary information
    """

    method_decorators = [authenticate]

    def get(self):
        """
        One may either provide a CSV string of `tags` desired, or else provide duplicate query string `tag` values
        which the API will then put together in a CSV list as needed by IbPy
        :return: JSON dict of dicts
        """
        choices = {"AccountType", "NetLiquidation", "TotalCashValue", "SettledCash", "AccruedCash", "BuyingPower",
                   "EquityWithLoanValue", "PreviousDayEquityWithLoanValue", "GrossPositionValue", "RegTEquity",
                   "RegTMargin", "SMA", "InitMarginReq", "MaintMarginReq", "AvailableFunds", "ExcessLiquidity",
                   "Cushion", "FullInitMarginReq", "FullMaintMarginReq", "FullAvailableFunds", "FullExcessLiquidity",
                   "LookAheadNextChange", "LookAheadInitMarginReq", "LookAheadMaintMarginReq",
                   "LookAheadAvailableFunds", "LookAheadExcessLiquidity", "HighestSeverity", "DayTradesRemaining",
                   "Leverage"}
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument('tags', type=str, help='CSV list of tags from this set: {}'.format(choices), trim=True)
        parser.add_argument('tag', type=str, action='append', help='Account information you want to see: {error_msg}',
                            trim=True, choices=choices, default=[])
        # NOTE beware that flask will reject GET requests if there's a Content-Type in the header with an error:
        # "message": "The browser (or proxy) sent a request that this server could not understand."

        args = parser.parse_args()
        # Make a master list of tags from all possible arguments
        tags = args['tag']
        tags += args['tags'].split(',') if args['tags'] is not None else []
        if len(tags) == 0:
            # No tags were passed, so throw an error
            return dict(message=dict(tags='Must provide 1 or more `tag` args, and/or a CSV `tags` arg')), 400
        # Reduce and re-validate
        tags = set(tags)
        if not tags.issubset(choices):
            return dict(message=dict(tags='All tags must be from this set: {}'.format(choices))), 400
        # re-create CSV list
        tags = ','.join(list(tags))
        # debug('TAGS: {}'.format(tags))
        return utils.make_response(sync.get_account_summary(tags))


class AccountUpdate(Resource):
    """ Resource to handle requests for account update information.
    """
    method_decorators = [authenticate]

    def get(self):
        """
        This endpoint does _not_ subscribe to account info (hence "Update" instead of "Updates" - use feed for that),
        but only gets latest info for given acctCode.
        :return: JSON dict of dicts
        """
        parser = reqparse.RequestParser()
        parser.add_argument('acctCode', type=str, help='Account number/code', trim=True, required=True)
        args = parser.parse_args()
        return utils.make_response(sync.get_account_update(args['acctCode']))

class Executions(Resource):
    """ Resource to handle requests for recent executions.
    """
    method_decorators = [authenticate]

    def get(self):
        """ Use optional filter params in querystring to retrieve execDetails from past 24hrs (IB API limitation):
        https://www.interactivebrokers.com/en/software/api/apiguide/java/executionfilter.htm
        """
        parser = reqparse.RequestParser()
        parser.add_argument('acctCode', type=str, help='Account number/code to Filter', trim=True, required=False)
        parser.add_argument('clientId', type=str, help='Client ID to Filter', trim=True, required=False)
        parser.add_argument('exchange', type=str, help='Exhange to Filter', trim=True, required=False)
        parser.add_argument('secType', type=str, help='Security Type to Filter', trim=True, required=False)
        parser.add_argument('side', type=str, help='Side to Filter', trim=True, required=False)
        parser.add_argument('time', type=str, help='Time (yyyymmdd-hh:mm:ss) to Filter', trim=True, required=False)

        args = parser.parse_args()
        return utils.make_response(sync.get_executions(args))


class ExecutionCommissions(Resource):
    """ Resource to get CommissionReports from past executions.  No guarantee as DB is wiped every time docker container
    is created.
    """
    method_decorators = [authenticate]

    def get(self, execId=None):
        """ Retrieves details of filled orders using stored data in SQLite DB
        """
        parser = reqparse.RequestParser(bundle_errors=True)
        parser.add_argument('execId', type=int, required=False,
                            help='Execution ID to get CommissionReport for')
        args = parser.parse_args()
        execId = args.get('execId')

        if execId is None:
            # Get all filled orders available
            resp = Commissions.query.all()
        else:
            resp = Commissions.query.filter(Commissions.exec_id == execId).first()
        resp = [r.commission_report for r in resp]
        return utils.make_response(resp)




class ClientState(Resource):
    """ Explore what the connection state for client connection to TWS
    """
    method_decorators = [authenticate]

    def get(self):
        resp = dict(connected=dict())
        resp['connected'][g.client_id] = g.client_connection.isConnected()
        return utils.make_response(resp)


class Beacon(Resource):
    def get(self):
        """ A GET here causes a PUT to our GAE App with needed info.  GETs initiated by GAE or a cron job with:
        */5 13-20 * * 1-5 curl -k  https://localhost/beacon
        """
        agent = request.headers.get('User-Agent')
        ip = request.remote_addr  # 172.17.0.1 means from container host

        # log.debug('Agent: "{}", IP: {}'.format(agent, request.remote_addr))
        # If done with curl locally: curl/7.26.0
        if agent != 'AppEngine-Google; (+http://code.google.com/appengine; appid: s~orion-minute)' \
                and ip != '172.17.0.1':
            msg = 'Unexpected user agent ({}) or IP ({})'.format(agent, ip)
            log.error(msg)
            return None, 400

        return send_flare_to_gae()


class Test(Resource):
    def get(self):
        resp = {k: str(v) for k, v in request.environ.iteritems()}

        log.debug('Environment vars: {}'.format(resp))
        return resp


class Hello(Resource):
    def get(self):
        return dict(msg="Hello World!  Here's info on the client used to connect to IBGW",
                    clientId=g.client_id,
                    connected=g.client_connection.isConnected())


# ---------------------------------------------------------------------
# ROUTING
# ---------------------------------------------------------------------
api.add_resource(History, '/history/<string:symbol>')
api.add_resource(Market, '/market/<string:symbol>')
api.add_resource(Order, '/order')
api.add_resource(OrderOCA, '/order/oca')
api.add_resource(OrderFilled, '/order/filled')
api.add_resource(PortfolioPositions, '/account/positions')
api.add_resource(AccountSummary, '/account/summary')
api.add_resource(AccountUpdate, '/account/update')
api.add_resource(Executions, '/executions')
api.add_resource(ExecutionCommissions, '/executions/commissions')
api.add_resource(ClientState, '/clients')
api.add_resource(Beacon, '/beacon')
api.add_resource(Test, '/test')
api.add_resource(Hello, '/')

# ---------------------------------------------------------------------
# SETUP CLIENTS
# ---------------------------------------------------------------------


log.debug('Using IB GW client at: {}:{}'.format(g.client_connection.host, g.client_connection.port))

if __name__ == '__main__':
    host = os.getenv('IBREST_HOST', '127.0.0.1')
    port = int(os.getenv('IBREST_PORT', '443'))
    client_id = g.client_id

    # Set up our client connection with IBGW
    client = ibConnection(g.ibgw_host, g.ibgw_port, client_id)
    connection.setup_client(client)
    client.connect()
    g.client_connection = client
    # g.clientId_pool = [client_id]

    # Call our own beacon code to register with GAE
    if g.serializer is not None:
        log.debug('Sent flare to GAE with response: {}'.format(send_flare_to_gae()))
    else:
        log.debug('No beacon flare sent.  No ID_SECRET_KEY found in environment: {}.  Client ID: {}'.format(os.getenv('ID_SECRET_KEY'), client_id))


    # When runnning with werkzeug, we already get good logging to stdout, so disabble loggers
    # root.setLevel(logging.ERROR)
    log.debug('Setting up IBREST at {}:{}'.format(host, port))
    #context = ('ibrest.crt', 'ibrest.key')

    # Log to file to since Docker isn't doing it for use
    # Add rotating file log handler
    # from logging.handlers import TimedRotatingFileHandler
    #
    # hdlr_file = TimedRotatingFileHandler('ibrest.log', when='D', backupCount=5)
    # hdlr_file.setLevel(logging.DEBUG)
    # hdlr_file.setFormatter(logging.Formatter(log_format))
    # logging.getLogger().addHandler(hdlr_file)



    DEBUG = False
    # For HTTPS with or without debugging
    # app.run(debug=DEBUG, host=host, port=port, ssl_context=context)
    # app.run(debug=DEBUG, host=host, port=port)



    # For HTTP (take note of port)
    app.run(debug=False, host=host, port=port, threaded=True)
