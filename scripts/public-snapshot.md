<!-- Changes: operator steps for the local public snapshot. Push stays manual. -->

# Public snapshot

Build a history-free tree for `valinagacevschi/BronchoRoute`. The script does
not push, does not create the GitHub repository, and does not call `gh`.

Run it from the private checkout with the pipeline environment (the script
imports termcolor):

```sh
export BRONCHOROUTE_PRIVATE_TERMS='Testname'
# Comma-separated. Matching is case-insensitive and accent-folded.
# Set this in the shell only. Do not write the names into a file, a log, or the snapshot.

OUT="${TMPDIR:-/tmp}/bronchoroute-public"
rm -rf "$OUT"
cd pipeline && uv run python ../scripts/public-snapshot.py "$OUT"
```

`$OUT` must be empty or absent, and must not be inside this repository.
`--ref` defaults to `HEAD` (`git archive` of that commit, not the work tree).
`--version` defaults to `desktop/package.json`.

The script prints:

- every excluded path and why
- every comment line it removed because it named `git.xelasys.ro` or
  `lung-nav.appcenter.ro` (a host anywhere except a comment line aborts)
- scanner waivers and, on failure, `file:line`
- the licence check
- one local commit, `BronchoRoute <version> — public snapshot`, authored
  `Vali Nagacevschi <vali@appcenter.ro>`
- `origin` `https://github.com/valinagacevschi/BronchoRoute.git`
- the push command, which it does not run

`SNAPSHOT_MANIFEST.txt` is written next to `$OUT`, not inside it, so it is not
part of the commit. It lists the source commit and a sha256 for each file.

## What stays out

Internal briefs, `docs/`, the lung-nav web deploy (`deploy/`, `Dockerfile`,
`.dockerignore`, `.gitlab-ci.yml`, the container and case-staging scripts),
`sponsors.json`, and operator scripts other than `scripts/third_party_notices.py`.

`prototype/renderer.html` stays. The server serves `renderer.template.html`.
`desktop/lib.test.js` and `pipeline/test_topology_rotation_playwright.py`
(`test_template_splice`) read `renderer.html` to check that embed. The embed is
the same anonymized airway graph as `pipeline/cases/1_graph.json`: no name,
UID, or date. The scanner still has to accept it.

`pipeline/test_public_snapshot.py` is a pipeline test, so a later snapshot of a
commit that contains it will include the test. It skips when
`scripts/public-snapshot.py` is absent. That script is not on the allow-list.

## Review, then publish

1. Read the excluded list, the rewrite lines, the waiver lines, and the manifest.
2. Confirm `git -C "$OUT" log --oneline` is one commit and `git -C "$OUT" remote -v`
   shows origin with no remote-tracking branches.
3. Create the empty GitHub repository `valinagacevschi/BronchoRoute` yourself.
4. Run the printed push command yourself:

```sh
git -C "$OUT" push -u origin main
```
