#!/usr/bin/env bash
# ha_target.sh — the ONE access layer to a Home Assistant target.
#
# Targets are NAMED PROFILES of the same abstraction, declared in
# ha/sailing-dash/.env (see .env.template), differing only in transport (local
# docker vs ssh + docker) — never in code path. Any number of profiles may exist
# (several Pi5 boxes side by side); "stage" and "prod" are just the two defaults.
# Source this file and call `ha_target_init <profile> [user@host]`, then use:
#
#   ha_exec <cmd...>              run a command inside the HA container
#   ha_mkdir <dir>                mkdir -p inside the container
#   ha_cat <file>                 cat a file from the container (stdout)
#   ha_cp_to_container <src> <dst>  copy a local file into the container
#   ha_cp_dir_to_container <src> <dst>  copy a local directory into the container
#   ha_restart                    restart the container
#
# After init these are set: HA_TRANSPORT, HA_HOST, HA_CONTAINER, HA_CONFIG_DIR.

HA_TARGET_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# lib/ lives in ha/sailing-dash/helpers/lib/ — the subproject root is two up.
HA_TARGET_SCRIPT_DIR="$(cd "${HA_TARGET_LIB_DIR}/../.." && pwd)"
HA_TARGET_PROJECT_ROOT="$(cd "${HA_TARGET_SCRIPT_DIR}/../.." && pwd)"

# The profile itself comes from ha/sailing-dash/.env ONLY. The repo root
# deploy.conf is deliberately NOT sourced here: it describes the PROD ydnu-02
# gateway (HA_CONTAINER=homeassistant) and reading it used to make a stage deploy
# silently target the production container.
# shellcheck source=lib/env_profile.sh
source "${HA_TARGET_LIB_DIR}/env_profile.sh"

ha_target_init() {
    local env_name="$1"
    local host_arg="${2:-}"

    # Legacy generic override, still honoured: HA_CONTAINER=ha-test ./deploy.sh --stage.
    # It is captured before the profile is resolved, and no repo-root config is
    # sourced any more, so it can only come from the caller's own environment.
    local container_override="${HA_CONTAINER:-}"

    env_profile_load "${env_name}" || return 1

    if [[ -n "${container_override}" ]]; then
        HA_CONTAINER="${container_override}"
        export HA_CONTAINER
    fi

    if [[ -n "${host_arg}" ]]; then
        HA_HOST="${host_arg}"
    elif [[ "${HA_TRANSPORT}" == "local-docker" ]]; then
        HA_HOST="${HA_SSH_HOST:-localhost}"
    else
        HA_HOST="${HA_SSH_HOST}"
    fi

    if [[ "${HA_TRANSPORT}" == "ssh-docker" && -z "${HA_HOST}" ]]; then
        echo "ERROR: no ssh host for the '${env_name}' target." >&2
        echo "       Pass it explicitly (./deploy.sh --target ${env_name} user@host) or set" >&2
        echo "       $(env_profile_prefix "${env_name}")_SSH_HOST in ${ENV_PROFILE_FILE}" >&2
        echo "       (cp ${HA_TARGET_SCRIPT_DIR}/.env.template ${ENV_PROFILE_FILE})." >&2
        return 1
    fi

    SSH="ssh -o ConnectTimeout=8 ${HA_HOST}"
    SCP="scp -q"
    export HA_TRANSPORT HA_HOST HA_CONTAINER HA_CONFIG_DIR
    # Kept for backwards compatibility with scripts/messages using DEPLOY_HOST.
    DEPLOY_HOST="${HA_HOST}"
}

_ha_is_local() { [[ "${HA_TRANSPORT}" == "local-docker" ]]; }

ha_exec() {
    if _ha_is_local; then
        docker exec "${HA_CONTAINER}" "$@"
    else
        ${SSH} "sudo docker exec ${HA_CONTAINER} $*" < /dev/null
    fi
}

ha_mkdir() {
    if _ha_is_local; then
        docker exec "${HA_CONTAINER}" mkdir -p "$1" 2>/dev/null || true
    else
        ${SSH} "sudo docker exec ${HA_CONTAINER} mkdir -p $1" 2>/dev/null || true
    fi
}

ha_cat() {
    if _ha_is_local; then
        docker exec "${HA_CONTAINER}" cat "$1" 2>/dev/null
    else
        ${SSH} "sudo docker exec ${HA_CONTAINER} cat $1" 2>/dev/null
    fi
}

ha_cp_to_container() {
    local src="$1" dest="$2"
    if _ha_is_local; then
        docker cp "${src}" "${HA_CONTAINER}:${dest}"
    else
        local filename
        filename="$(basename "${src}")"
        ${SCP} "${src}" "${HA_HOST}:/tmp/${filename}" < /dev/null
        ${SSH} "sudo docker cp /tmp/${filename} ${HA_CONTAINER}:${dest} && rm -f /tmp/${filename}" < /dev/null
    fi
}

# Delivers a whole directory (e.g. build/deps/nmea2000/custom_components/nmea2000)
# into the container: `docker cp` locally, `scp` + `docker cp` over SSH.
ha_cp_dir_to_container() {
    local src="${1%/}" dest="$2"
    ha_mkdir "$(dirname "${dest}")"
    if _ha_is_local; then
        docker cp "${src}/." "${HA_CONTAINER}:${dest}"
    else
        local base tarball
        base="$(basename "${src}")"
        tarball="$(mktemp -t ha_target_dir).tgz"
        tar -czf "${tarball}" -C "$(dirname "${src}")" "${base}"
        ${SCP} "${tarball}" "${HA_HOST}:/tmp/${base}.tgz" < /dev/null
        ${SSH} "set -e; rm -rf /tmp/${base}.d; mkdir -p /tmp/${base}.d; \
                tar -xzf /tmp/${base}.tgz -C /tmp/${base}.d; \
                sudo docker exec ${HA_CONTAINER} mkdir -p ${dest}; \
                sudo docker cp /tmp/${base}.d/${base}/. ${HA_CONTAINER}:${dest}; \
                rm -rf /tmp/${base}.tgz /tmp/${base}.d" < /dev/null
        rm -f "${tarball}"
    fi
}

# ── Idempotent delivery: never re-upload what is already in place ────────────
# A deploy used to copy every card bundle, every integration file and both
# storage documents on every run, then restart Home Assistant — even when
# nothing had changed. The helpers below make delivery content-addressed:
#
#   ha_local_sha256 <file>            sha256 of a local file (macOS + Linux)
#   ha_remote_sha256 <remote_file>    sha256 of the file inside the container
#   ha_tree_manifest <dir>            "sha256  relpath" lines, sorted (a tree id)
#   ha_cp_to_container_if_changed <src> <dst>       copy only on mismatch
#   ha_cp_dir_to_container_if_changed <src> <dst>   copy only on tree mismatch
#
# Both *_if_changed helpers return 0 when they copied something and 1 when they
# skipped, and they keep the counters HA_DELIVERED / HA_SKIPPED so a caller can
# decide whether a container restart is needed at all.
HA_DELIVERED=0
HA_SKIPPED=0

# The deploy state file records the tree manifest hash of every directory we
# delivered. Directories are compared through it because hashing a whole tree
# inside the container on every run costs more than the copy it would save.
HA_STATE_PATH="/config/.storage/sailing_deploy_state"

ha_local_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

_ha_stdin_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{print $1}'
    else
        shasum -a 256 | awk '{print $1}'
    fi
}

# Hashes the file's CONTENT as the container sees it, so a file edited by hand
# on the target is detected as changed and gets overwritten.
ha_remote_sha256() {
    local remote="$1" out
    out="$(ha_cat "${remote}" | _ha_stdin_sha256 || true)"
    # sha256 of an empty stream — the file does not exist / is unreadable.
    if [[ "${out}" == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" ]]; then
        echo ""
    else
        echo "${out}"
    fi
}

ha_tree_manifest() {
    local dir="${1%/}"
    # No -print0/sort -z here: BSD sort on macOS has no reliable -z, and no
    # artifact we deliver has a newline in its name.
    ( cd "${dir}" && find . -type f | LC_ALL=C sort | \
        while IFS= read -r f; do echo "$(ha_local_sha256 "${f}")  ${f}"; done )
}

ha_state_load() {
    HA_STATE_FILE="$(mktemp -t sailing_deploy_state)"
    ha_cat "${HA_STATE_PATH}" > "${HA_STATE_FILE}" 2>/dev/null || true
    export HA_STATE_FILE
}

ha_state_get() {
    [[ -n "${HA_STATE_FILE:-}" && -f "${HA_STATE_FILE}" ]] || return 0
    awk -v k="$1" '$1 == k {print $2}' "${HA_STATE_FILE}" | tail -1
}

ha_state_set() {
    [[ -n "${HA_STATE_FILE:-}" ]] || ha_state_load
    local tmp
    tmp="$(mktemp -t sailing_deploy_state_new)"
    if [[ -f "${HA_STATE_FILE}" ]]; then
        awk -v k="$1" '$1 != k' "${HA_STATE_FILE}" > "${tmp}"
    fi
    echo "$1 $2" >> "${tmp}"
    mv "${tmp}" "${HA_STATE_FILE}"
    HA_STATE_DIRTY=1
}

ha_state_flush() {
    [[ "${HA_STATE_DIRTY:-0}" == "1" ]] || return 0
    ha_cp_to_container "${HA_STATE_FILE}" "${HA_STATE_PATH}"
    HA_STATE_DIRTY=0
}

ha_cp_to_container_if_changed() {
    local src="$1" dest="$2" label="${3:-$(basename "$1")}"
    if [[ "${HA_FORCE_DELIVERY:-0}" != "1" ]]; then
        local local_hash remote_hash
        local_hash="$(ha_local_sha256 "${src}")"
        remote_hash="$(ha_remote_sha256 "${dest}")"
        if [[ -n "${remote_hash}" && "${local_hash}" == "${remote_hash}" ]]; then
            echo "  = ${label} unchanged — skipped"
            HA_SKIPPED=$((HA_SKIPPED + 1))
            return 1
        fi
    fi
    echo "  + ${label} -> ${HA_CONTAINER}:${dest}"
    ha_cp_to_container "${src}" "${dest}"
    HA_DELIVERED=$((HA_DELIVERED + 1))
    return 0
}

ha_cp_dir_to_container_if_changed() {
    local src="${1%/}" dest="$2" label="${3:-$(basename "${1%/}")}"
    local manifest_hash
    manifest_hash="$(ha_tree_manifest "${src}" | _ha_stdin_sha256)"
    if [[ "${HA_FORCE_DELIVERY:-0}" != "1" ]]; then
        [[ -n "${HA_STATE_FILE:-}" ]] || ha_state_load
        if [[ "$(ha_state_get "${dest}")" == "${manifest_hash}" ]]; then
            echo "  = ${label} unchanged — skipped"
            HA_SKIPPED=$((HA_SKIPPED + 1))
            return 1
        fi
    fi
    echo "  + ${label} -> ${HA_CONTAINER}:${dest}"
    ha_cp_dir_to_container "${src}" "${dest}"
    ha_state_set "${dest}" "${manifest_hash}"
    HA_DELIVERED=$((HA_DELIVERED + 1))
    return 0
}

ha_restart() {
    if _ha_is_local; then
        echo "Restarting local container ${HA_CONTAINER} ..."
        docker restart "${HA_CONTAINER}"
    else
        echo "Restarting remote container ${HA_CONTAINER} on ${HA_HOST} ..."
        ${SSH} "sudo docker restart ${HA_CONTAINER}"
    fi
}

ha_container_running() {
    if _ha_is_local; then
        [[ "$(docker inspect -f '{{.State.Running}}' "${HA_CONTAINER}" 2>/dev/null)" == "true" ]]
    else
        [[ "$(${SSH} "sudo docker inspect -f '{{.State.Running}}' ${HA_CONTAINER} 2>/dev/null" < /dev/null)" == "true" ]]
    fi
}
