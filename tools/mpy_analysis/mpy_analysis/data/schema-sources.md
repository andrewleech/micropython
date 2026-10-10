# Offline SBOM schema sources

CycloneDX JSON validation uses the unmodified official 1.6 schema and its SPDX/JSF companion schemas from https://github.com/CycloneDX/specification/tree/1.6/schema. Runtime validation resolves only bundled resources; it does not fetch schemas or dependency metadata.

| File | SHA-256 | Source |
|---|---|---|
| bom-1.6.schema.json | 3e92dddbc30cf7f6a02b80f0942b1a4cfd4fb1c26f1dfc4310afa9d613cafb93 | https://raw.githubusercontent.com/CycloneDX/specification/1.6/schema/bom-1.6.schema.json |
| spdx.schema.json | baa9d3bd1ed57b6751b0887edead6b5063ff53ff7429cf85d476c6c94af0166e | https://raw.githubusercontent.com/CycloneDX/specification/1.6/schema/spdx.schema.json |
| jsf-0.82.schema.json | 8bae002c25e723db7ee1f26afde680ae1a2b1a8f6b4b4b0fd65dc3becb090aae | https://raw.githubusercontent.com/CycloneDX/specification/1.6/schema/jsf-0.82.schema.json |
| spdx-2.3.schema.json | 239208b7ac287b3cf5d9a9af23f9d69863971102a5e1587a27a398b43490b89b | https://raw.githubusercontent.com/spdx/spdx-spec/v2.3/schemas/spdx-schema.json |

The CycloneDX and JSF schema comments declare Apache-2.0. The SPDX identifier companion is shipped as part of that schema set. [schema-Apache-2.0.txt](schema-Apache-2.0.txt) is the unmodified [upstream licence](https://raw.githubusercontent.com/CycloneDX/specification/1.6/LICENSE), including its OWASP Foundation notice.

The separate **SPDX2.3** schema comes from the SPDX specification project's `v2.3` tag (Git blob `ee61e6686e885f8139c132647fd0b4f483b8fb81`). Its title is preserved in the schema. The repository's [licence](https://raw.githubusercontent.com/spdx/spdx-spec/v2.3/LICENSE) is CC-BY-3.0, copied unmodified as [schema-CC-BY-3.0.txt](schema-CC-BY-3.0.txt). No separate schema-specific copyright-holder or licence notice was present in the inspected schema or schema directory. This attribution does not claim upstream endorsement.

All four schema files are unmodified. Their redistribution terms are separate from the surrounding tooling's licence.
