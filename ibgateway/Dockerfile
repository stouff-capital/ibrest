FROM phusion/baseimage:0.9.22
MAINTAINER Sebastian Fialka <sebastian.fialka@sebfia.net>

CMD ["/sbin/my_init"]
# install xvfb and other X dependencies
RUN apt-get update -y \
    && apt-get install -y wget xvfb libxrender1 libxtst6 x11vnc socat unzip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /tmp
RUN wget https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh && \
    chmod a+x *.sh && \
    echo "n" | ./ibgateway-stable-standalone-linux-x64.sh && \
    rm -rf /tmp/*

RUN wget https://github.com/ib-controller/ib-controller/releases/download/3.4.0/IBController-3.4.0.zip && \
    unzip IBController-3.4.0.zip -d ~/IBController && \
    chmod -R 777 ~/IBController && \
    rm -rf /tmp/*


COPY init/xvfb_init /etc/init.d/xvfb
COPY init/vnc_init /etc/init.d/vnc
COPY bin/xvfb-daemon-run /usr/bin/xvfb-daemon-run
COPY bin/run-installer /usr/bin/run-installer


EXPOSE 5900
EXPOSE 4003
EXPOSE 4004

ENV DISPLAY :0

COPY bin/runscript.sh /usr/bin/runscript.sh
CMD bash runscript.sh

CMD ["/usr/bin/run-installer"]
# CMD ["/usr/bin/runscript.sh"]
