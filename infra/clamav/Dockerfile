# syntax=docker/dockerfile:1.7

ARG CLAMAV_BASE_IMAGE=clamav/clamav:1.5@sha256:f0954d679017eb6d48221e2b2be3ac5457bf278a844f39b672376f55a085f591
FROM ${CLAMAV_BASE_IMAGE}

RUN addgroup -S -g 1000 vefaas-clamav \
    && adduser -S -D -H -u 1000 -G vefaas-clamav -s /sbin/nologin vefaas-clamav

COPY --chmod=0444 clamd.conf /etc/clamav/vefaas-clamd.conf
COPY --chmod=0444 freshclam.conf /etc/clamav/vefaas-freshclam.conf
COPY --chmod=0555 healthcheck.sh /usr/local/bin/clamav-healthcheck
COPY --chmod=0555 reload-clamd.sh /usr/local/bin/reload-clamd
COPY --chmod=0555 start-clamav.sh /usr/local/bin/start-clamav

EXPOSE 3310/tcp

HEALTHCHECK --start-period=360s --interval=30s --timeout=5s --retries=3 \
    CMD ["/usr/local/bin/clamav-healthcheck"]

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/start-clamav"]
