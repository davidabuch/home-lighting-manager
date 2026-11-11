# Cabinet recovery: deployment provenance

Authorization: source engineering, offline tests and green review PR only. No deployment,
reload, restart, ownership reset or physical commands. The October 21 audit/contract governs.

## Selected source

Independently rechecked GitHub main: `2e59932cb74d6b67a3d9dbe9f16ee8fc1f8ae861`.
The original clean homeowner checkout at `6647a75` remains untouched. Development uses a
separate clone/branch. The JSON manifest beside this document contains SHA-256 identities
of the relevant files on main and pre-110 `932ef24`.

## Live evidence

Fresh read-only configuration retrieval matches the audit:

| Component | Loaded configuration hash | Confidence / preservation |
|---|---|---|
| Cabinet | `eede36cafeb09f19` | Verified config: parallel, Daily-only default release; not repository queued evaluator branch |
| Spa Session Manager | `04c0bce6a4124640` | Verified config; preserve startup recovery, thermostat alias and marked stop OFF |
| Spa Temperature Color | `8c6d6a7163853fa7` | Verified config; preserve thermostat alias and explicit consequence registration |
| HLM | observing, generation 368, command_authority false | Interface verified; Python identity unknown |
| Legacy executor | healthy; current Main underlay Daily | Interface verified; Python identity unknown |
| HACS label | v0.2.40 | Label only; source manifest still 0.2.39 |

The user-reported rollback of promotion_observer.py and rendering.py from pre-110 is
**not independently verified**, either on disk or in imported modules. Backup path:
`/config/hlm-deployment-backups/rollback-110-20261021-182550`.
No supported non-invasive file identity interface was found in the available configuration,
state and app/log interfaces. No execution bridge, app startup command, restart or storage
edit is used to obtain hashes. YAML script lookup was unavailable in the audit; retained
resolver traces establish its current interface, not its full source. Unknown fields in
the manifest remain null, never inferred from HACS or healthy sensors.

## Deployment implications

Offline development can safely use current main's known projection/renderer interface;
this does not certify the mixed installed code. Preserve F01/F02 and PR95/97/108/109 in source.
Do not change cross-surface invalidation or Spa/49ers policies in this repair. Cabinet work
must ship as matching HLM metadata + legacy executor + cabinet automation/package changes.
HLM-only HACS installation is insufficient. Before separately approved deployment, obtain
the installed component manifest and back up the exact mixed installation; do not replace
Spa YAML or unrelated package functionality wholesale. The external scene monitor and
Backyard overlay producers are dependencies, not proven bundled source.
