# k8s manifests

Tiny Kubernetes deployment of the ollive stack. Designed for k3s on a single
node, but the manifests are plain k8s — they'll work on any cluster.

## Topology

```
                       host nginx (80/443, Let's Encrypt cert)
                                      │
                                      ▼  ollive.hardeep.cv
                       ┌──────────────┴──────────────┐
                       │   proxy_pass to NodePorts   │
                       │   /chat/* /stats/* etc →    │
                       │   127.0.0.1:30001 (backend) │
                       │   / →                       │
                       │   127.0.0.1:30000 (frontend)│
                       └──────────────┬──────────────┘
                                      ▼
                              k3s NodePort
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │            namespace=ollive │                             │
        │   ┌───────────┐   ┌──────────┴────────┐   ┌────────────┐  │
        │   │  frontend │   │     backend       │   │            │  │
        │   │ (Next.js) │   │  (FastAPI +       │   │            │  │
        │   │  Deploy   │   │   aggregation +   │◀─▶│   redis    │  │
        │   └─────┬─────┘   │   log consumer)   │   │ (events)   │  │
        │         │         │  Deploy / NodePort│   └────────────┘  │
        │         │         └──────────┬────────┘                   │
        │         │                    │                            │
        │         │                    ▼                            │
        │         │              ┌──────────┐                       │
        │         │              │ postgres │                       │
        │         │              │   PVC    │                       │
        │         │              └──────────┘                       │
        └─────────┴────────────────────────────────────────────────┘
```

## First-time install

```bash
# 1) Install k3s without traefik/servicelb (we use host nginx + NodePort).
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC=\
  "server --disable=traefik --disable=servicelb --write-kubeconfig-mode=644" sh -

# 2) Build the app images on the box and load into k3s' containerd.
cd /root/ollive
docker build -t ollive/backend:latest  ./backend
docker build --build-arg NEXT_PUBLIC_API_BASE_URL=https://ollive.hardeep.cv \
  -t ollive/frontend:latest ./frontend
docker image save ollive/backend:latest  | k3s ctr images import -
docker image save ollive/frontend:latest | k3s ctr images import -

# 3) Create the secret (fill in real values first).
cp deploy/k8s/11-secret.template.yaml /root/ollive-secret.yaml
$EDITOR /root/ollive-secret.yaml

# 4) Apply everything.
k3s kubectl apply -f deploy/k8s/00-namespace.yaml
k3s kubectl apply -f deploy/k8s/10-config.yaml
k3s kubectl apply -f /root/ollive-secret.yaml
k3s kubectl apply -f deploy/k8s/20-postgres.yaml
k3s kubectl apply -f deploy/k8s/21-redis.yaml
k3s kubectl apply -f deploy/k8s/30-backend.yaml
k3s kubectl apply -f deploy/k8s/31-frontend.yaml

# 5) Point host nginx at the NodePorts (see ../nginx/ollive-k8s).
ln -sf /etc/nginx/sites-available/ollive-k8s /etc/nginx/sites-enabled/ollive
nginx -t && systemctl reload nginx
```

## Re-deploying after a code change

```bash
cd /root/ollive
docker build -t ollive/backend:latest  ./backend
docker build --build-arg NEXT_PUBLIC_API_BASE_URL=https://ollive.hardeep.cv \
  -t ollive/frontend:latest ./frontend
docker image save ollive/backend:latest  | k3s ctr images import -
docker image save ollive/frontend:latest | k3s ctr images import -

k3s kubectl -n ollive rollout restart deploy/backend deploy/frontend
k3s kubectl -n ollive rollout status  deploy/backend deploy/frontend
```

## Scaling the backend

The backend is stateless from the chat-handler POV; the Redis consumer group
splits work across replicas (each pod gets a unique `REDIS_CONSUMER_NAME` via
the downward API). To scale:

```bash
k3s kubectl -n ollive scale deploy/backend --replicas=3
```

Postgres and Redis are single-replica with PVCs (Recreate strategy). Scaling
either of those is a real HA project, not a knob.
