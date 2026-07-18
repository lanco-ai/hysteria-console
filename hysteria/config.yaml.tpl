listen: :443

tls:
  cert: /root/hysteria/server.crt
  key: /root/hysteria/server.key

auth:
  type: http
  http:
    url: http://127.0.0.1:8082/auth

obfs:
  type: salamander
  salamander:
    password: __HY_OBFS_PASSWORD__

trafficStats:
  listen: 127.0.0.1:25413
  secret: __HY_API_SECRET__

masquerade:
  type: proxy
  proxy:
    url: https://www.bing.com
    rewriteHost: true
