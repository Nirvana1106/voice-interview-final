ARG NGINX_BASE_IMAGE=nginx:1.27-alpine
FROM ${NGINX_BASE_IMAGE}
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html style.css app.js samples.json /usr/share/nginx/html/
EXPOSE 80
