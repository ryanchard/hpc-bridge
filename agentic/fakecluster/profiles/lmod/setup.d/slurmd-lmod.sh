#!/usr/bin/env bash
# `lmod` overlay, slurmd: enable the module system here (workers replay env_setup on the compute nodes).
# shellcheck disable=SC1091
source "$PROFILE_DIR/lmod-enable.sh"
