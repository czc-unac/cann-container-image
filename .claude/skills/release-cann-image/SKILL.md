---
name: CANN Image Release Engineer
description: You are a software development engineer responsible for automating and executing the CANN Docker image release pipeline.
---

# CANN Image Release Process

## When to use this skill
Use this skill when a new CANN software version is released to generate all necessary code and configuration for building and publishing the corresponding Docker images.

## Preferred path: the deterministic generator

For any version whose name matches `tools/cann_release/release_profiles.json` (normal stable/beta releases), do NOT walk the steps below manually. Run instead:

```
python3 tools/cann_release/generator.py <version> [--link-id <x.y.Tn>]
```

- Beta releases require the OBS `CANN x.y.Tn` directory as `--link-id` (visible in the bulletin's download links; it cannot be derived from the version string). Stable releases derive their path from the version and must not pass it.
- The generator probes OBS for the exact packages first, builds the release matrix from `tools/cann_release/release_matrix.json` (declarative: add or drop a chip, OS or Python version there - one line - and re-run), renders Dockerfiles through `tools/template.py` (scoped to the new version), registers publish JSONs, extends the four workflow ymls, rewrites the marker-fenced OVERVIEW blocks and prepends the supported_tags section, then self-checks. The previous release only supplies ordering and formatting templates, and a real historical release was replayed byte-for-byte during development.
- In CI this same path is triggered by a maintainer commenting `/release-cann <version> [x.y.Tn]` on that version's "New CANN release detected: <version>" issue (`.github/workflows/release_cann_agent.yml`). It generates on top of the upstream release repository, pushes the branch to this repository and posts a compare link; opening the upstream PR stays a human one-click action.
- Both the monitor and the generator require the FULL OBS package set (toolkit, nnal and per-chip ops for every current chip and both arches). Announcements whose packages are incomplete (e.g. `9.1.0-beta.2`, where only the 950 archives were ever uploaded) are reported to the run summary as "announced but not buildable" and never open an issue or a PR: that is CANN's condition not being met, not a maintainer decision. Only if a partial-platform build is genuinely intended, run the manual steps below with an explicit matrix.

Use the manual steps below only when the generator refuses (irregular version naming such as `8.3.rc2`, or a matrix-drift gate trip). When it refuses because the community introduced a new naming style or chip/OS baseline, prefer encoding that into `tools/cann_release/release_profiles.json` / `tools/cann_release/release_matrix.json` and re-running.

## Release Workflow

### Step 1: Update Beta Version Template (Conditional)
Action: Check if the specified CANN version string contains "beta".

If YES: Update the ALPHA_DICT mapping within `/tools/template.py`. Add a new entry using the CANN version as the key and its corresponding download link identifier as the value.

If NO: Proceed to Step 2. No changes to template.py are required.

### Step 2: Generate Build Configuration for Standard Environments
Action: Modify the `build_cann_arg.json` file.

Configuration Basis:

CANN Version: The newly specified version.

Authoritative current baseline: `tools/cann_release/release_matrix.json` is the single source of truth for the generated matrix. To add or drop a chip, OS or Python version, edit that file (one line) and re-run; do not hand-edit the arg files.

Target Chips (cann_chip): 910b, 310p, A3, 910, 950

Operating Systems (os): openeuler (24.03), ubuntu (22.04)

Python Version (py-version): 3.12, 3.11, 3.10

Tag Naming Convention: <cann-version>-<chip>-<os><os-version>-<py-version>

Goal: Create a build matrix in the JSON file that covers all combinations of the above parameters for standard OS images.

### Step 3: Generate Build Configuration for ManyLinux Environments
Action: Modify the `build_manylinux_arg.json` file.
Configuration Basis:

CANN Version: The newly specified version.

Target Chips (cann_chip): 910b, 310p, A3, 950

Operating System (os): manylinux (2_34)

Python Version (py-version): 3.12 ,3.11, 3.10

Tag Naming Convention: <cann-version>-<chip>-<os><os-version>-<py-version>

Goal: Create a build matrix for the cross-platform manylinux compatibility images.

### Step 4: Execute Template Engine to Generate Dockerfiles
Action: Run the template engine script.

```
python3 tools/template.py
```

Outcome: This will generate the `cann/` and `manylinux/` directories containing the Dockerfiles and the finalized build_*_arg.json configuration files for the build process.

### Step 5: Register New Image Tags for Publication
Action: Update the version registry JSON files.

Update `cann_publish_version.json` with the new tags generated for standard environments.

Update `manylinux_publish_version.json` with the new tags generated for manylinux environments.

Purpose: This step registers the new version tags, making them available for the CI/CD pipeline to build and publish.

### Step 6:  Update CI/CD Workflow Definitions
Action: Edit the GitHub Actions workflow files.

Modify `.github/workflows/build_and_push_cann.yml`.

Modify `.github/workflows/build_and_push_manylinux.yml`.

Change Required: Add the newly generated image tags (from Step 2 & 3) to the list of valid inputs for the workflow_dispatch trigger. This allows the new images to be manually triggered for build.

Modify `.github/workflows/batch_build_and_push_cann.yml` .

Modify `.github/workflows/batch_build_and_push_manylinux.yml` .

### Step 7:  Update OVERVIEW
Action: Edit the OVERVIEW files.

Modify `OVERVIEW.md`.

Modify `OVERVIEW.zh.md`.

Change Required: the generator owns only the blocks fenced by `<!-- RELEASED:BEGIN/END -->` and `<!-- LATEST:BEGIN/END -->` - the released sentence and the LATEST heading/table/note. Keep those markers in place when editing manually and change only what is inside them. The tag-naming example row and the quick-start `CANN_TAG=` samples live outside the markers: they are documentation samples (still valid tags) and are left to humans.

### Step 8:  Update supported_tags
Action: update `supported_tags.md`

Purpose: Add the latest CANN version section (plain and `-devel` rows) before the first existing `### CANN <version>` heading.



