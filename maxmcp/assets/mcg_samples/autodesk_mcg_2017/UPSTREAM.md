# Autodesk 3ds Max MCG Samples

This directory contains a pinned, read-only subset of
[ADN-DevTech/3dsMax-MCG-Samples](https://github.com/ADN-DevTech/3dsMax-MCG-Samples).

- Upstream commit: `820d8a32badf916849282081ca42416a88d2f6b7`
- Upstream target: 3ds Max 2017
- Included: `Tools/**/*.maxtool`, `Compounds/**/*.maxcompound`, and `LICENSE`
- Excluded: `Scenes`, `Packages`, generated `.ms` and `.txt` files, and all other assets

The graphs are discovery and forking references. 3dsmax-mcp does not install
them into 3ds Max. An agent must fork a selected graph into its temporary MCG
workspace, then validate, compile, and verify that copy against the active
3ds Max version. A fork takes the compounds its graph is built from with it
(most tools here use `-ext` compounds only this pack carries), and each compile
loads those into the running session's operator depot; they are gone when
3ds Max exits.
