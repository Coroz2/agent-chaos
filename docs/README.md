# Agent Chaos Documentation

This directory separates stable project direction from released behavior and historical planning.

## Authority map

| Document | Authority |
| --- | --- |
| [Project Vision](PROJECT-VISION.md) | Stable mission, audience, principles, capability families, and product boundaries. |
| [v0.2 Specification](specs/v0.2.md) | Authoritative contract for the current released v0.2 behavior. |
| [v0.1 Specification](specs/v0.1.md) | Immutable contract for the released v0.1 behavior. |
| [Current Specification](PROJECT-SPEC.md) | Compatibility pointer to the latest released version specification. |
| [Release Guide](RELEASING.md) | Packaging, tagging, publishing, and release-verification procedure. |
| [Initial Plan](archive/INITIAL-PLAN.md) | Historical source material that motivated v0.1; not an active contract or roadmap. |

The root [README](../README.md) is the user-facing introduction and quick start. It summarizes the
product but does not replace the project vision or a versioned specification.

## Versioned specifications

Specifications live under `docs/specs/` and use the release line in their filename, such as
`v0.1.md`. Each specification declares one state:

- **Draft:** incomplete planning material; not approved for implementation.
- **Approved:** decision-complete scope authorized for implementation but not yet released.
- **Released:** immutable record of shipped behavior. Only clearly labeled factual corrections or
  errata may change it.
- **Superseded:** planning document replaced before release by another approved specification. A
  released specification is never marked superseded merely because a newer version exists.

Future behavior belongs in a new draft specification. Do not add unreleased behavior to a released
specification.

## Planning policy

Detailed release sequencing, roadmaps, and implementation prompts are added only after focused
planning and explicit approval. They are temporary planning authorities, not substitutes for the
stable project vision or released specifications.

Changes to the project vision require the focused proposal described in
[its change policy](PROJECT-VISION.md#decision-and-change-policy). Changes to public behavior must
identify the affected versioned contract and document their compatibility strategy.
