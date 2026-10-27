# lung-nav deploy (main k8s)

Viewer-only image at `https://lung-nav.appcenter.ro`. No TotalSegmentator, no
patient DICOM in the image. Case data is a static hostPath mount.

## Staged files (local-001)

Only these paths under the pipeline layout are required on the node
(`/volumes/lung-nav-data/…`). Regenerable crops/labels are **not** uploaded — the pod
writes them under `/cache`.

| Relative path |
|---------------|
| `data/extracted/local/local-001/case.json` |
| `data/extracted/local/local-001/local-001_CT.nii.gz` |
| `ts_out/local-001/lobes.nii.gz` |
| `ts_out/local-001/lobes/trachea.nii.gz` |
| `ts_out/local-001/airways.nii.gz` |
| `ts_out/local-001/airway_bridges.nii.gz` |
| `ts_out/local-001/airway_bridges.json` |
| `out/local-001/local-001_graph.json` |

Stage locally:

```bash
scripts/stage-case-data.sh local-001 /tmp/lung-nav-stage
```

## Upload to node `k8s`

1. Create the host directory with a short-lived alpine pod (nodeSelector → `k8s`):

```bash
kubectl -n lung-nav run lung-nav-seed --rm -it --restart=Never \
  --image=alpine:3.20 \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"k8s"},"containers":[{"name":"seed","image":"alpine:3.20","command":["sleep","3600"],"volumeMounts":[{"name":"data","mountPath":"/data"}],"securityContext":{"runAsUser":0}}],"volumes":[{"name":"data","hostPath":{"path":"/volumes/lung-nav-data","type":"DirectoryOrCreate"}}]}}'
```

2. From another terminal, copy the staged tree (files only):

```bash
kubectl -n lung-nav cp /tmp/lung-nav-stage/. lung-nav-seed:/data/
```

3. Inside the seed pod, make the tree read-only, then delete the pod:

```bash
chmod -R a-w /data
exit   # deletes the pod (--rm)
```

## Basic auth Secret (out of band — not in git)

```bash
htpasswd -c -B /tmp/lung-nav.htpasswd USERNAME
kubectl -n lung-nav create secret generic lung-nav-basic-auth \
  --from-file=auth=/tmp/lung-nav.htpasswd
rm -f /tmp/lung-nav.htpasswd
```

Ingress annotation `nginx.ingress.kubernetes.io/auth-secret: lung-nav-basic-auth`
expects this Secret.

## Apply / rollback

CI `deploy` job (manual on `main`) applies namespace → pull-secret copy →
pv/pvc → envsubst deployment/service/ingress → rollout status.

Rollback to the previous image tag:

```bash
export IMAGE="$CI_REGISTRY_IMAGE:<previous-sha>"
envsubst '${IMAGE}' < deploy/deployment.yaml | kubectl apply --validate=false -f -
kubectl -n lung-nav rollout status deployment/lung-nav --timeout=300s
```

Or `kubectl -n lung-nav rollout undo deployment/lung-nav`.

## Local container smoke

```bash
scripts/stage-case-data.sh local-001 /tmp/lung-nav-stage
docker build --platform linux/amd64 -t lung-nav:local .
scripts/container-smoke.sh lung-nav:local /tmp/lung-nav-stage
```
