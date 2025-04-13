#!/bin/bash

# This script checks if new MICROPY_ or MPY_ settings added in a commit
# also have corresponding entries in Kconfig files.

# --- Configuration ---
# Files to check for new settings defines/variables
CHECK_FILES_PATTERN='\.(c|h|mk|cmake)$|CMakeLists\.txt$'
# Exclude patterns (grep -E regex)
EXCLUDE_DIRS='(^|/)build|^lib/|^tests/|^tools/|^examples/|^docs/'
# Regex to find settings (captures the setting name)
# Group 1: Define, Group 2: Make, Group 3: CMake
SETTING_REGEX='(?:#define\s+(MICROPY_[A-Z0-9_]+|MPY_[A-Z0-9_]+))|(?:^\s*(MICROPY_[A-Z0-9_]+|MPY_[A-Z0-9_]+)\s*[:?]?=)|(?:set\s*\(\s*(MICROPY_[A-Z0-9_]+|MPY_[A-Z0-9_]+))'
KCONFIG_CHECK_REGEX='config\s+'

# --- Script Logic ---

echo "Checking for new MICROPY_/MPY_ settings missing Kconfig entries..."

# Determine files changed compared to the base commit
# Use different methods for pre-commit vs CI (push/pr)
if [ -n "$PRE_COMMIT" ]; then
    # In pre-commit hook, compare staged changes to HEAD
    CHANGED_FILES=$(git diff --name-only --cached HEAD | grep -E "$CHECK_FILES_PATTERN" | grep -Ev "$EXCLUDE_DIRS" || true)
elif [ -n "$GITHUB_EVENT_NAME" ] && [ "$GITHUB_EVENT_NAME" == "pull_request" ]; then
    # In GitHub Actions PR, compare with the base branch
    BASE_REF=$(jq -r .pull_request.base.ref "$GITHUB_EVENT_PATH")
    # Fetch base branch history if needed (shallow clones might miss it)
    git fetch origin "+refs/heads/$BASE_REF:refs/remotes/origin/$BASE_REF"
    CHANGED_FILES=$(git diff --name-only "origin/$BASE_REF"...HEAD | grep -E "$CHECK_FILES_PATTERN" | grep -Ev "$EXCLUDE_DIRS" || true)
elif [ -n "$GITHUB_EVENT_NAME" ] && [ "$GITHUB_EVENT_NAME" == "push" ]; then
    # In GitHub Actions push, compare with the previous commit
    # Note: This only checks the last commit, might need adjustment for multi-commit pushes
    CHANGED_FILES=$(git diff --name-only HEAD^ HEAD | grep -E "$CHECK_FILES_PATTERN" | grep -Ev "$EXCLUDE_DIRS" || true)
else
    # Fallback for local testing (compare staged changes)
    CHANGED_FILES=$(git diff --name-only --cached HEAD | grep -E "$CHECK_FILES_PATTERN" | grep -Ev "$EXCLUDE_DIRS" || true)
fi

if [ -z "$CHANGED_FILES" ]; then
    echo "No relevant files changed."
    exit 0
fi

# Find all unique settings added in the changed files
FOUND_SETTINGS=$( (git diff --cached HEAD -- $CHANGED_FILES || git diff HEAD^ HEAD -- $CHANGED_FILES) | grep -E "^\+" | grep -oE "$SETTING_REGEX" | sed -E 's/#define\s+|\s*[:?]?=|set\s*\(\s*//g' | sort -u || true)
# Alternate using git show for pre-commit?
# FOUND_SETTINGS=$(git show :./$CHANGED_FILES | grep -oE "$SETTING_REGEX" | sed -E 's/#define\s+|\s*[:?]?=|set\s*\(\s*//g' | sort -u)

if [ -z "$FOUND_SETTINGS" ]; then
    echo "No new MICROPY_/MPY_ settings found in changed files."
    exit 0
fi

# Check if these settings exist in any Kconfig file
MISSING_SETTINGS=""
ALL_KCONFIG_FILES=$(find . -name 'Kconfig*' -not -path "*/build/*" -not -path "./lib/*")

for setting in $FOUND_SETTINGS; do
    # Check if the setting already exists as a config option
    # Use grep -q for efficiency
    if ! grep -q -E "${KCONFIG_CHECK_REGEX}${setting}\b" $ALL_KCONFIG_FILES; then
        MISSING_SETTINGS="$MISSING_SETTINGS $setting"
    fi
done

# Report results
if [ -n "$MISSING_SETTINGS" ]; then
    echo "--------------------------------------------------"
    echo "Error: Found new MICROPY_/MPY_ settings without corresponding Kconfig entries:"
    for missing in $MISSING_SETTINGS; do
        echo "  - $missing"
    done
    echo ""
    echo "Please add a 'config $missing' entry to the appropriate Kconfig file."
    echo "--------------------------------------------------"
    exit 1
else
    echo "All new MICROPY_/MPY_ settings seem to have Kconfig entries. OK."
    exit 0
fi
