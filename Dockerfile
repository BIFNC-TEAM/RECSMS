ARG BASE_IMAGE=node:22-bookworm-slim
FROM ${BASE_IMAGE}
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources && apt-get update && apt-get install -y --no-install-recommends python3 python3-venv && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY requirements-auth.txt requirements-production.txt ./
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN python3 -m venv /opt/platform-env && /opt/platform-env/bin/pip install --index-url "${PIP_INDEX_URL}" --no-cache-dir -r requirements-production.txt
ENV PATH="/opt/platform-env/bin:${PATH}"
COPY . .
RUN npm run build && mkdir -p /app/data && chown -R node:node /app
USER node
EXPOSE 8765
CMD ["python3", "-u", "server.py", "--host", "0.0.0.0", "--port", "8765"]
