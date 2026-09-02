# Home Lighting Manager

A Home Assistant lighting-orchestration package for deterministic ownership of several Philips Hue lighting surfaces.

This repository contains the production Home Lighting Manager package and the closely coupled Liquor Cabinet Display Lighting automation.

## Managed surfaces

The current installation manages:

- Main Area
- Holiday Front Eve Zone
- Driveway / Holiday Path
- Backyard
- Front-yard pillar lights

The exact entity IDs in this repository reflect the production installation and may need adaptation for another Home Assistant instance.

## Ownership model

Lighting is controlled by an explicit priority model rather than independent automations blindly issuing light commands.

### Main Area

Highest to lowest:

1. Hue Entertainment Sync
2. Homeowner Manual ownership
3. 49ers live-game lighting
4. Holiday lighting
5. Daily baseline
6. Off

### Front Eve

Highest to lowest:

1. Homeowner Manual ownership
2. 49ers live-game lighting
3. Holiday lighting
4. Daily baseline
5. Off

### Backyard

Highest to lowest:

1. Hue Entertainment Sync
2. Homeowner Manual ownership
3. Spa Gauge
4. Holiday lighting
5. Daily baseline
6. Off

### Driveway Path

Highest to lowest:

1. Homeowner Manual ownership
2. Holiday lighting
3. Daily baseline
4. Off

## HA-command attribution guards

Home Assistant-owned commands use per-zone restartable guards.

Every HA lighting command re-arms its zone guard with a deliberate OFF -> ON transition. Each zone has its own `mode: restart` eight-second auto-clear automation.

This prevents a previous correlation timer from expiring during a newer Hue scene recall and falsely classifying an HA-owned recall as homeowner Manual activity.

This behavior was added after a reproduced Holiday -> 49ers transition exposed exactly that race.

## Raw Hue scene monitoring

This repository also includes the bundled `home_hue_scene_monitor` custom integration under:

`custom_components/home_hue_scene_monitor`

It observes raw Philips Hue scene-recall state and supplies the attribution evidence Home Lighting Manager needs for Manual ownership and dynamic-scene restoration.

The lighting manager consumes:

- `sensor.main_area_last_recall`
- `sensor.front_eve_last_recall`
- `sensor.path_last_recall`
- `sensor.backyard_last_recall`

A raw Hue scene recall can assert Manual ownership even when the aggregate HA light entity was already ON.

## Manual ownership

External Hue scene recalls and OFF -> ON activity can assert Homeowner Manual ownership.

Turning a manually controlled surface OFF relinquishes Manual ownership and causes the authoritative evaluator to reconstruct the correct lower-priority owner.

HA-owned changes must never assert Manual ownership.

## Dynamic Hue scene restoration

When Manual Main Area ownership originates from a raw Hue scene recall, the exact Hue scene UUID is stored.

This is particularly important for the liquor-cabinet override. When a cabinet door opens, the liquor bulb is temporarily forced to bright 4000 K white. When the doors close, the original Hue scene is recalled so that the bulb rejoins Hue's native dynamic palette rather than merely returning to a static sampled color.

## Liquor Cabinet Display Lighting

The automation in:

`automations/liquor_cabinet_display_lighting.yaml`

is intentionally stored with this project because it is tightly coupled to the Home Lighting Manager's:

- Main Area Manual ownership
- HA-command guard
- Hue Sync ownership
- scene-context helper
- snapshot helpers
- central evaluator

When either liquor-cabinet door opens outside Hue Sync, the cabinet light becomes full-brightness 4000 K white.

When both doors close:

- a raw Hue Manual scene is re-recalled so the bulb rejoins the native dynamic scene;
- otherwise the captured static state is restored;
- if Manual ownership is not active, authority returns to the central Home Lighting evaluator.

## Holiday lighting

Holiday intelligence comes from the separate `home_holiday_calendar` integration.

Supported automatic lighting mappings currently include:

- New Year -> Golden Star
- Valentine's Day -> Valentine's Day
- St. Patrick's Day -> St. Patrick's Day
- Independence Day -> 4th of July
- Halloween -> Trick or Treat
- Thanksgiving -> Pumpkin Spice
- Christmas -> Jolly
- Hanukkah -> Hanukkah

Holiday ownership begins at sunset minus two hours.

The previous evening remains authoritative after midnight until the 01:59 local boundary. Startup reconciliation reconstructs Holiday ownership if Home Assistant was offline during the normal transition.

Unsupported calendar holidays do not take lighting ownership.

## 49ers integration

The 49ers scoring/celebration automation is intentionally **not included in this repository**.

Home Lighting Manager only consumes the Team Tracker state:

`sensor.nfl_san_francisco_49ers`

The central evaluator treats state `IN` as a live game and gives 49ers lighting priority over Holiday lighting.

The separate game automation remains responsible for score celebrations and periodic theme reassertion.

### Commissioned crossover behavior

The following regression was exercised live:

1. Halloween Holiday lighting active
2. Main Area and Front Eve display `Trick or treat`
3. Simulated 49ers live state asserted
4. Main Area and Front Eve display `49ers!`
5. Manual ownership remains OFF
6. 49ers live state released
7. Both zones return to `Trick or treat`
8. Manual ownership remains OFF

Result: PASS.

## Holiday commissioning

The Holiday implementation was exercised with:

- live raw Hue scene detection
- Manual priority over Holiday
- Holiday ownership on Main Area, Front Eve, Path, and Backyard
- HA-owned recall attribution
- Holiday -> 49ers -> Holiday crossover
- restart recovery
- after-midnight previous-evening recovery
- 01:59 release boundary
- sunset-minus-two-hours activation
- unsupported-holiday rejection
- dynamic Hue scene restoration after liquor-cabinet override

A synthetic startup decision matrix passed 8/8 cases.

A final production restart after temporary test code removal completed without Home Lighting startup errors.

## Installation

Copy:

`packages/home_lighting_manager.yaml`

into the Home Assistant packages directory.

The package assumes the required Hue scenes, helpers, binary sensors, Team Tracker entity, Spa Gauge helpers, and the Home Hue Scene Monitor integration already exist.

The liquor-cabinet automation can be imported or merged from:

`automations/liquor_cabinet_display_lighting.yaml`

## Related projects

- Home Holiday Calendar
- Separate 49ers celebration automation

Home Hue Scene Monitor is bundled directly in this repository.

## Production safety

Do not commit Home Assistant `.storage`, databases, access tokens, Hue API keys, backup files, or test-state files.

The Home Hue Scene Monitor retrieves the Hue application key at runtime from the existing Home Assistant Hue config entry; no Hue credential is stored in this repository.
