# 1.27.3+ потрібна для параметра resolve в upstream у відкритій версії nginx
FROM nginx:1.27.5-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY snippets/ /etc/nginx/snippets/
COPY html/ /usr/share/nginx/html/
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1/api/health || exit 1
