# Scene recall coverage: evidence and correction

Evidence: supplied `home_lighting_codex_runtime_bundle_20260908_070336`, using scene `group.rid`, group children/device light services, and HA registry UUIDs. Current light state was not used as desired-state truth.

| Surface | Hue resource | Resource ID | Before | After |
|---|---|---|---|---|
| Main Area | Holiday Main Area zone | `17ab93f4-24f5-4d8c-bbf1-ad0d5006b258` | Watched | Watched |
| Front Eve | Holiday Front Eve Zone | `5f73b760-dd70-4131-ad03-5452c271d8e4` | Watched | Watched |
| Path | Holiday Path zone | `d1e5ec88-d6e5-4dc7-9bf6-1113be8d6bb9` | Watched | Watched |
| Path | Driveway Path Lights zone | `0ba84dd4-0fe1-4440-9b65-ea7ea1feff35` | Omitted | Watched |
| Backyard | Holiday Backyard zone | `873874ab-e1d2-473f-8430-2ef2fafb5a15` | Watched | Watched |
| Backyard | Backyard zone | `87285951-8893-497d-bd77-f618bd5b82e9` | Omitted | Watched |
| Backyard | Backyard room | `bd7521e2-86b3-4679-8de1-e585c1089d66` | Omitted | Watched |

`scene.driveway_path_lights_meriete` maps to Hue scene `5894647f-6fd0-4fb1-bc00-a374c87315c8`, recalled against the Driveway Path Lights zone. `scene.backyard_backyard_forest_adventure` maps to `d3f4e83c-b9d2-405a-be84-93bdb37230a9`, recalled against the Backyard room.

The two Path groups have the same six lights. All three Backyard resources have the same eleven lights. The room's twelve child resources must not be mistaken for twelve lights: resolve device services. Main Area has seven lights; Front Eve has one. Celebration's eight lights are exactly Main Area plus Front Eve.

Before correction, the coordinator filtered only on the single Holiday group ID. A newer external recall against either Daily group was excluded from the result. With the aggregate light already ON, neither the scene timestamp nor ON/OFF trigger could assert Manual. This is a real coverage gap, not merely naming differences.

The narrow correction adds equivalent group IDs to the existing per-surface configuration and chooses the latest `status.last_recall` across them. Sensor names, unique IDs, polling interval, four-surface boundaries, guard semantics, and Main Area retained scene UUID remain unchanged. `hue_group_id` retains its primary-group meaning; new attributes `monitored_hue_group_ids` and `recalled_hue_group_id` make coverage/source explicit. No partial room groups, unrelated scenes, or master zone are added.

The Manual detector's recall triggers now use `to: null`, which reacts to timestamp state changes and excludes attribute-only changes. This is necessary to keep `active`, scene counts, or new source metadata from masquerading as a new homeowner recall. HA documents this distinction in [state triggers](https://www.home-assistant.io/docs/automation/trigger/#state-trigger). Brightness and XY are still not Manual evidence.

Offline coverage tests exercise the coordinator's actual filtering with newer equivalent-group recalls. Separate actual HA state-trigger tests show that all four existing detector paths assert Manual while already ON, ignore attribute-only updates, and honor active HA guards. The reconciler also fails closed when a more recent equivalent-group recall makes scene intent ambiguous.

Physical Hue-app commissioning remains pending: recall an alternate scene in every row's group while ON, confirm its corresponding Manual helper asserts, then repeat with an HA-owned recall and confirm the guard prevents false Manual. Validate topology again if Hue rooms/zones are edited. The intentionally narrow change does not reinterpret partial-room recalls as whole-surface Manual ownership.
