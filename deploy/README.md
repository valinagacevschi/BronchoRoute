<!-- # Changes: realism model staging, bearer keys, separate ingress and verification (#58). -->
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
pv/pvc → envsubst deployment/service/ingress/ingress-models → rollout status.
The fixed file list in `.gitlab-ci.yml` includes `deploy/ingress-models.yaml`.

Rollback to the previous image tag:

```bash
export IMAGE="$CI_REGISTRY_IMAGE:<previous-sha>"
envsubst '${IMAGE}' < deploy/deployment.yaml | kubectl apply --validate=false -f -
kubectl -n lung-nav rollout status deployment/lung-nav --timeout=300s
```

Or `kubectl -n lung-nav rollout undo deployment/lung-nav`.

## Realism model hosting

`/models/<name>` uses per-user bearer keys; the viewer keeps its existing basic
auth. No keys configured means all model requests return 404. Only files named in
`/data/models/manifest.json` are downloadable. GET and HEAD support Range requests;
responses include `X-Model-SHA256` and `Cache-Control: no-store`.

Create a private `keys.json` outside the repository, with one independently
generated random key per user: `{"alice":"<random-key>","bob":"<another-key>"}`.
Use restrictive file permissions (`chmod 600`) and never commit the file or keys.

```bash
kubectl -n lung-nav create secret generic lung-nav-model-key \
  --from-file=keys.json=/private/path/keys.json
kubectl -n lung-nav rollout restart deployment/lung-nav
```

To revoke a user, remove their entry from the private JSON, replace the Secret,
and restart the deployment (environment variables do not refresh in running pods):

```bash
kubectl -n lung-nav create secret generic lung-nav-model-key \
  --from-file=keys.json=/private/path/keys.json --dry-run=client -o yaml | kubectl apply -f -
kubectl -n lung-nav rollout restart deployment/lung-nav
kubectl -n lung-nav rollout status deployment/lung-nav --timeout=300s
```

Stage `broncho-flow-int8.onnx` and its matching `model.json` under a local
`/tmp/lung-nav-model-stage/models/` directory. Generate the manifest locally:

```bash
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path('/tmp/lung-nav-model-stage/models')
files = []
for name in ('broncho-flow-int8.onnx', 'model.json'):
    path = root / name
    with path.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    files.append({'name': name, 'sha256': digest, 'size': path.stat().st_size})
assert files[0]['size'] == 580180539
assert files[0]['sha256'] == '419adbd774b0c0dad2eae9746c923b4f9265d5f1ab3a908fef9b3b2a784b58df'
(root / 'manifest.json').write_text(json.dumps({'files': files}, indent=2) + '\n')
PY
```

Use the `lung-nav-seed` pod procedure above on node `k8s`. From another terminal,
copy just the model directory, preserving existing case data. If replacing an
existing read-only model directory, first run `chmod -R u+w /data/models` inside
the seed pod. Copy the manifest last, after both files finish uploading:

```bash
kubectl -n lung-nav exec lung-nav-seed -- mkdir -p /data/models
kubectl -n lung-nav cp /tmp/lung-nav-model-stage/models/broncho-flow-int8.onnx lung-nav-seed:/data/models/broncho-flow-int8.onnx
kubectl -n lung-nav cp /tmp/lung-nav-model-stage/models/model.json lung-nav-seed:/data/models/model.json
kubectl -n lung-nav cp /tmp/lung-nav-model-stage/models/manifest.json lung-nav-seed:/data/models/manifest.json
kubectl -n lung-nav exec lung-nav-seed -- chown -R 10001:10001 /data/models
kubectl -n lung-nav exec lung-nav-seed -- chmod -R a+rX,a-w /data/models
```

Exit the seed pod to remove it. The application continues to mount `/data`
read-only. The PV declares 4Gi; the PVC keeps its 2Gi request because a bound
static claim cannot be resized and CI re-applies `pvc.yaml` on every deploy.
hostPath capacity is not enforced, so check the node has ~600 MB free under
`/volumes/lung-nav-data`.

Apply the separate Ingress after deploying the server and Secret:

```bash
kubectl apply -f deploy/ingress-models.yaml
curl -i https://lung-nav.appcenter.ro/models/broncho-flow-int8.onnx
# Expect 401 and WWW-Authenticate: Bearer once keys and files are staged.
# Set MODEL_KEY privately in your shell; do not paste a real key into docs.
curl -sS -D /tmp/model-headers -o /tmp/model-range \
  -H "Authorization: Bearer $MODEL_KEY" -H 'Range: bytes=10-19' \
  https://lung-nav.appcenter.ro/models/broncho-flow-int8.onnx
cat /tmp/model-headers
wc -c /tmp/model-range
# Expect 206, Content-Range: bytes 10-19/580180539, and 10 bytes.
```

The models Ingress has no basic-auth annotations and limits requests to 5/minute
per IP. Authenticated requests log key ID, first forwarded IP (or peer), file,
and Range, never the key. Roll back public bearer access with:

```bash
kubectl -n lung-nav delete ingress lung-nav-models
```

The main Ingress then handles `/models/` with viewer basic auth as well as the
server's bearer check. Remove the model Secret and restart to disable the route
entirely. A later CI deployment reapplies the models Ingress.

## Local container smoke

```bash
scripts/stage-case-data.sh local-001 /tmp/lung-nav-stage
docker build --platform linux/amd64 -t lung-nav:local .
scripts/container-smoke.sh lung-nav:local /tmp/lung-nav-stage
```
