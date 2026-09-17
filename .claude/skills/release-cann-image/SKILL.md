---
name: CANN Image Release Engineer
description: You are a software development engineer responsible for automating and executing the CANN Docker image release pipeline.
---

# CANN Image Release Process

## When to use this skill
Use this skill when a new CANN software version is released to generate all necessary code and configuration for building and publishing the corresponding Docker images.

## Preferred path: the deterministic generator

For any version whose name matches `tools/release_profiles.json` (normal stable/beta releases), do NOT walk the steps below manually. Run instead:

```
python3 tools/gen_cann_release.py <version> [--link-id <x.y.Tn>]
```

- Beta releases require the OBS `CANN x.y.Tn` directory as `--link-id` (visible in the bulletin's download links; it cannot be derived from the version string). Stable releases derive their path from the version and must not pass it.
- The generator probes OBS for the exact packages first, clones the previous release's matrix from the arg files, renders Dockerfiles through `tools/template.py` (scoped to the new version), registers publish JSONs, extends the four workflow ymls and updates OVERVIEW/supported_tags, then self-checks. It was verified by golden replay to reproduce real release commits byte-for-byte.
- In CI this same path is triggered by a maintainer commenting `/release-cann <version> [x.y.Tn]` on that version's "New CANN release detected: <version>" issue (`.github/workflows/release_cann_agent.yml`), which pushes the branch and opens a draft PR.

Use the manual steps below only when the generator refuses (irregular version naming such as `8.3.rc2`, or a matrix-drift gate trip). When it refuses because the community introduced a new naming style or chip/OS baseline, prefer encoding that into `tools/release_profiles.json` / `tools/release_matrix.json` and re-running.

## Release Workflow

### Step 1: Update Alpha/Beta Version Template (Conditional)
Action: Check if the specified CANN version string contains "alpha" or "beta".

If YES: Update the ALPHA_DICT mapping within `/tools/template.py`. Add a new entry using the CANN version as the key and its corresponding download link identifier as the value.

If NO: Proceed to Step 2. No changes to template.py are required.

### Step 2: Generate Build Configuration for Standard Environments
Action: Modify the `build_cann_arg.json` file.

Configuration Basis:

CANN Version: The newly specified version.

Authoritative current baseline: `tools/release_matrix.json`. The real matrix for a new release is cloned from the newest version's entries in the arg file itself; only deviate from that when the maintainer asks, and then update `tools/release_matrix.json` in the same PR.

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

Modify `D:\cann-container-image\.github\workflows\batch_build_and_push_cann.yml` .

Modify `D:\cann-container-image\.github\workflows\batch_build_and_push_manylinux.yml` .

### Step 7:  Update OVERVIEW
Action: Edit the OVERVIEW files.

Modify `D:\cann-container-image\OVERVIEW.md`.

Modify `D:\cann-container-image\OVERVIEW.zh.md`.

Change Required：Update the CANN version to the latest version. In addition, the latest CANN version needs to be added to the Tag Naming Rules table.

### Step 8:  Update supported_tags
Action： update `D:\cann-container-image\supported_tags.md`

Purpose：Add the latest CANN.



