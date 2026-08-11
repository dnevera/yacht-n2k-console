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
