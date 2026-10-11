# Submodule review and integration automation

## Goals

Make a downstream change pass through one deterministic path:

1. Machine-check the child repository pull request against atelier-core's
   machine-enforceable rules and Python syntax.
2. After the child pull request is merged, propose its exact merge commit as a
   gitlink update in every parent variant that contains that submodule.
3. Run assembly checks and project tests on each parent update pull request.
4. Keep GitHub and Forgejo synchronized as Git-ref mirrors without treating
   their pull request approvals as synchronized state.

Child checks do not run parent assembly or integration tests. Parent pull
requests are the integration and machine-test boundary. Codex PR review is
explicitly deferred and is not part of the current rollout.

## Repositories and responsibilities

| Component | Responsibility |
|---|---|
| atelier-core | Provide the machine-check command/rules used by child PR workflows. |
| Managed child repositories | On GitHub pull requests, run atelier-core machine checks and Python syntax checks; on merge, send the exact merge SHA to the parent coordinator. |
| atelier-studio | Coordinate submodule events, resolve configured gitlinks, create/update parent pointer PRs, and run project assembly/tests on those PRs. |
| atelier-reel-solo and atelier-reel-creator | Receive pointer update PRs when their `.gitmodules` contains the changed child; run their own assembly/tests before merge. |
| atelier-sync | Continue bidirectional synchronization of repository refs. It does not transfer PR review or approval state. |
| Forgejo | Mirror Git refs. GitHub remains the only PR review and merge authority for managed repositories. |

Submodule discovery is based on each parent's `.gitmodules`, not a fixed list
or fixed number of domains. External and nested third-party submodules are not
treated as Atelier-managed repositories unless explicitly configured.

## End-to-end flow

### Child pull request

- GitHub is the sole review/merge entry point.
- Run checks against the exact pull request head:
  - atelier-core's machine-enforceable source review/discipline checks;
  - syntax validation for the repository's supported source languages (for
    example, Python compilation for Python sources and the existing TypeScript
    compiler check for the frontend).
- These are the only child-repository machine gates. Do not run parent assembly
  or project integration tests here.
- Do not label an unconfigured check as passed. The workflow must fail if its
  required core checker cannot be fetched or executed.

### Child merge and pointer proposal

- A successful merge sends the exact merged commit SHA, repository identity,
  target ref, and event ID to the coordinator. Never resolve the event to a
  later moving branch head.
- Resolve the repository to one or more gitlink paths by reading each parent
  repository's `.gitmodules`.
- Create or update a bot branch and pull request in each affected parent
  variant. Do not push gitlink changes directly to a protected parent branch.
- Coalesce duplicate deliveries for the same repository/ref/SHA. Serialize
  writes per parent/ref, and stop with an actionable error on non-fast-forward
  or conflicting parent changes rather than choosing a side.
- For a managed GitHub repository, only GitHub-originated merged changes can
  initiate pointer proposals. A Forgejo-only master update is not an approval
  and must first be mirrored to GitHub.

### Parent pointer pull request

- Confirm the proposed gitlink points to the exact child merge SHA supplied by
  the event and that the commit is fetchable.
- Run the parent's normal assembly check, relevant project tests, and existing
  policy checks on the complete candidate tree.
- Run `python -m src.main --check` and the portable project-test subset on the
  complete candidate tree. In the current test set, S01-S03 tests import the
  inference stack and the S06 production regression depends on a machine-local
  JSON fixture; these remain explicitly reported as not run, not counted as
  passed.
- The atelier-core discipline scan is a blocking check. Existing findings must
  be fixed in the owning child repository or explicitly resolved by changing
  the constitution/checker; do not turn scanner failures into warnings.
- A maintainer approves and merges the parent PR. The merged gitlink must still
  equal the reviewed SHA.
- Do not require or claim Codex PR review in this rollout. Machine-check
  findings must be reviewed and fixed or explicitly resolved against the
  constitution; do not silently suppress findings to make a check green.

### Bidirectional mirror

- Keep atelier-sync's existing Git-ref synchronization in both directions.
- GitHub Actions are authoritative for PR checks and merge decisions. Forgejo
  mirrors branches/tags/commits; it does not mirror PR approvals.
- Mirror-generated events are idempotently ignored or deduplicated using the
  repository/ref/SHA/event identity, so synchronization cannot create a loop
  of pointer PRs.
- Protect managed default branches on both hosts from direct human pushes and
  Forgejo-side merges that bypass the GitHub review flow. Permit only the
  synchronization identity to mirror approved refs.

## Token inventory and permission record

Never store a token value in this document, logs, workflow output, or source
control. Keep using the existing `GH_SYNC_TOKEN`; do not create a replacement
secret unless a verified permission check proves it is insufficient.

| Token/secret | Verified information | Required capability for the target design | Verification status |
|---|---|---|---|
| `GH_SYNC_TOKEN` in GitHub Actions | Existing workflows use it to fetch private child repositories and dispatch synchronization/monitoring events. | Read private managed child repositories; write contents/dispatch to the coordinator and parent variant repositories; create/update parent pull requests. For a classic PAT, `repo` is expected to cover private repo contents and pull requests. For a fine-grained PAT, grant only Contents read on child repos, Contents read/write and Pull requests read/write on parent variants/coordinator, and Contents write on `atelier-sync` where dispatch/ref synchronization requires it. | Exact scopes cannot be read from an Actions secret. Existing repository documentation establishes prior use, not all permissions required by the new PR-creation flow. Verify the existing secret's scopes/selected repositories before rollout; do not print or copy its value. |
| `GH_SYNC_TOKEN` in Forgejo Actions | Existing Forgejo workflow documents use it to dispatch `atelier-sync`; the variant updater also uses an `ATELIER` secret for Forgejo clone/push. | Keep only the permissions required by existing mirror dispatch/ref-sync. The new parent pointer coordinator runs on GitHub and should not require Forgejo write credentials. | Forgejo secret values/scopes are not inspectable from workflow YAML. Verify the current secret configuration without revealing its value. |
| Current local GitHub CLI `GH_TOKEN` | `gh auth status` reported scopes `copilot`, `gist`, `project`, `read:org`, `repo`, `user`, `workflow`, and `write:plugin_gateway_connections` for the active `NomadAnget` login. | Not used as a substitute for repository Actions/Forgejo secrets. Its `repo` and `workflow` scopes indicate the interactive CLI login only. | Observed locally on 2026-10-12; distinct from `GH_SYNC_TOKEN`, so this does not prove the secret's scopes. |

Before rollout, verify `GH_SYNC_TOKEN` on GitHub using token metadata or its
owner's settings and record only the permission names, repository selection,
token type, and expiry here. Never record the token or a token fingerprint.
If the existing secret already satisfies the requirements, retain it without
re-entering it.

## Implementation sequence

1. Inspect atelier-core's checker interface and define a stable command for
   downstream PRs.
2. Add and test a reusable child PR workflow; roll its caller into every
   managed child repository, excluding third-party submodules.
3. Replace the current direct-push `bump-variants` behavior with an exact-SHA
   event and pull-request coordinator that discovers affected variants from
   `.gitmodules`.
4. Add parent pointer PR assembly checks and portable project tests.
5. Preserve and test atelier-sync bidirectional ref behavior, event
   deduplication, branch-protection assumptions, and loop prevention.
6. Verify the existing `GH_SYNC_TOKEN` capability before enabling parent PR
   creation. Update the verified scope row above without storing a secret.
7. Run an end-to-end test with a harmless child commit: child checks pass,
    exact-SHA pointer PRs are created, parent machine checks run, merge updates
    the gitlink, and Forgejo receives the same approved ref.

## Acceptance criteria

- Child PR checks run on the exact PR head and contain only core machine review
  plus language-appropriate syntax validation.
- A merged child SHA is never silently replaced by a newer branch head.
- Every affected parent variant receives a reviewable pointer PR; no updater
  writes directly to its default branch.
- Parent CI validates the candidate assembly and project tests. Project tests
  must disclose dependency/fixture coverage limits. Codex review is deferred
  and is not represented as an active gate.
- Duplicate/out-of-order events do not create conflicting or redundant PRs.
- GitHub and Forgejo converge on the same approved refs without syncing review
  metadata or causing dispatch loops.
- Existing token secrets are reused when permissions allow; their values never
  enter source, documentation, or logs.
