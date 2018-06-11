# IBREST
REST API for use with [Interactive Brokers TWS and IB Gateway](https://www.interactivebrokers.com/en/index.php?f=5041&ns=T)

## Summary
By using [Flask-RESTful](http://flask-restful-cn.readthedocs.org/en/0.3.4/) (and therefore [Flask](http://flask.pocoo.org/)), a web-based API is created which then uses [IbPy](https://github.com/blampe/IbPy) to connect to an instance of TWS or IB Gateway, and interact with Interactive Brokers.  This documentation will generally use "TWS" to mean "TWS or IBGateway"

## setup
- python **2.7** (from Dockerfile)

## deployment
### kubernetes
1. `kubectl create secret generic ibgateway --from-literal=vnc-password=<VNC_PASSWORD>`
1. `kubectl create -f ibgateway\deploy\kubernetes\ibgateway.yml`


## Intents
### Better Firewalling
This API should run on the same machine with TWS so that TWS can be set to only allow connections from the local host.  This provides a nice security feature, and lets IP access then be controlled by more advanced firewall software (ie to allow wildcards or IP ranges to access this REST API and therefore the TWS instance it interfaces with).

### Google App Engine
This API layer between your algorithm code and the IbPy API code is intended for use on Google App Engine where an algoritm may be operating within the GAE system, with TWS running on a Compute Engine VM (ie [Brokertron](http://www.brokertron.com/)).  TWS does not support wildcard IP address (which would be a security hole anyways), and GAE uses many IPs when making outbound connections from one's App (making it neigh impossible to list all possible IPs in TWS' whitelist).  However, this project will aim to stay generalized enough so that it can be used outside of GAE.  

For running flask as a Docker container, consider [this tutorial](http://containertutorials.com/docker-compose/flask-simple-app.html) or use this existing [docker-flask image](https://hub.docker.com/r/p0bailey/docker-flask/).

TODO: Consider using following GAE features:

1. [Logging messages to GAE logger](https://cloud.google.com/logging/docs/agent/installation)
2. [Storing IB messages to DataStore](https://cloud.google.com/datastore/docs/getstarted/start_python/)
3. [Using Task Queue to get requests from GAE](https://cloud.google.com/appengine/docs/java/taskqueue/rest/about_auth).  IB allows 8 `client_ids`, which will impose a limit of 8 "simultaneous" tasks at a time with IBREST, unless some kind of task queuing happens (ie [Celery](http://flask.pocoo.org/docs/0.10/patterns/celery/))

### Synchronous
The [IB API](https://www.interactivebrokers.com/en/software/api/api.htm) is designed to be asynchronous, which adds labor
to writing code to interface with it.  As IB message exchanges are pretty fast (a couple seconds at most), it's within
time margins for use with HTTP requests (~60sec timeouts).  Thus, the exposed RESTful API opens a user-friendly
synchronous interface with IB.

### Pythonic
Use [IbPyOptional](https://code.google.com/p/ibpy/wiki/IbPyOptional) (`ib.opt` module) maximally.

## REST API
As IBREST is built with IbPy, and IbPy is based on the IB Java API, then IBREST will aim to use maximally similar language as found in those APIs' documentation.  The Java API is broken into two main layers:

1. [EClientSocket](https://www.interactivebrokers.com/en/software/api/apiguide/java/java_eclientsocket_methods.htm) - the connection to TWS for _sending_ messages to IB.
2. [EWrapper](https://www.interactivebrokers.com/en/software/api/apiguide/java/java_ewrapper_methods.htm) - the message processing logic for messages _returned_ by IB.  Some messages are streamed in at intervals (ie subscriptions) and will not be exposed as a REST URI.  Such are marked `Unexposed data feed` below.

TODO: Consider creating an RSS feed endpoint for such "Unexposed data feed" data.

**NOTE:** As noted in [Synchronous], TWS only allows 8 connections (client ID's 0-7, where 0 has some special privileges).  
An IBREST client only uses one of these allowed connections.  To increase throughput, you can create a "proxy" (see
`proxy.Dockerfile`) to front 8 IBREST instances, thereby reducing latency in quick, successive calls.  

While Python objects are named `with_under_scores`, IbPy and its corresponding Java code uses camelCase.  The IBREST source code will use camelCase to imply a direct correlation with IbPy.  For obejcts which only pertain to IBREST inner logic, under_scored names will be used.

All endpoints return JSON formatted data using keys and values consistent with IbPy and IB Java APIs (case sensitive).

For security, HTTPS is used by default.  To create your own cert and key, try:
`openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout ibrest.key -out ibrest.crt`

### Endpoint Groups
The documentation for each of these layers contains these sections, after which IBREST will create endpoints groups when applicable.  An endpoint may provide either a synchronous reponse or an atom feed.

IB Documentation | REST endpoint | Sync or Feed
---------------- | ------------- | --------------------
Connection and Server | NA: Handled by Flask configuration
Market Data | /market/data | Feed
Orders| /order | Sync
Account and Portfolio | /account | Sync
Contract Details | /contract | Sync [TBD]
Executions | /executions | Sync
Market Depth | /market/depth | Feed [TBD]
News Bulletins | /news | Feed [TBD]
Financial Advisors | /financial | Feed [TBD]
Historical Data | /historical | Sync [TBD]
Market Scanners | /market/scanners | Feed [TBD]
Real Time Bars| /bars | Feed [TBD]
Fundamental Data | /fundamental | Feed [TBD]
Display Groups| /displaygroups | Feed [TBD]


## API: app/main.py

```
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
```


### Synchronous Response Endpoint Details
These endpoints return a since single response.   

#### GET /order
A GET request retrieves a details for all open orders via `reqAllOpenOrders`.

#### POST /order
A POST request will generate a `placeOrder()` EClient call, then wait for the order to be filled .

#### DELETE /order
A DELETE request will call `cancelOrder()`.

#### GET /account/updates
A GET request to `/account/updates` will use `reqAccountUpdates()` to return messages received from `updateAccountValue/AccountTime()` and `updatePortfolio` EWrapper messages, as triggered by `accountDownloadEnd()`.

#### GET /account/summary
A GET request to `/account/summary` will use `reqAccountSummary()` to return messages received from `updateSummary()` EWrapper message as triggered by `accountSummaryEnd()`.

#### GET /account/positions
A GET request to `/account/positions` will use `reqPostions()` to return messages received from `position()` EWrapper message as triggered by `positionEnd()`.

### Atom Feed Endpoint Details
These endpoints are used for atom feeds.  They must be subscribed to or unsubscribed from.  They are not yet implemented
