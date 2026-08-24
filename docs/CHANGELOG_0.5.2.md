# 0.5.2 Object Identity and Execution Remediation

- Added conjunctive mandatory and ordered assistive AT-SPI identification properties.
- Added application, window, direct-parent, hierarchy, backend-attribute and explicit ordinal locator support.
- Removed implicit first-match resolution and arbitrary first-action activation fallbacks.
- Object capture now persists rich multi-property identity and refuses unresolved ambiguity unless explicitly disambiguated.
- Added genuine AT-SPI `present=false` observation for missing objects.
- Wired editable/captured component repositories into declarative plan validation and execution.
- Added `--components` to declarative plan validate/run CLI paths.
- Added full-path nested variable readiness validation.
- Added basic static literal input-type validation for registered step contracts.
- Declarative execution now consumes one authoritative transactional step output extraction.
- Step catalog metadata now includes an implementation SHA-256 digest.
- Replaced direct reference-client dependencies in tracking, threat, mosaic and triangulation semantic steps with typed backend-neutral services.
- No protected/production environment configuration was extended in this increment.
