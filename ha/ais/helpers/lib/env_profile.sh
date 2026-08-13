#!/usr/bin/env bash
# env_profile.sh — resolves a NAMED TARGET PROFILE from ha/ais/.env.
#
# Kept in sync with ha/sailing-dash/helpers/lib/env_profile.sh (same
# profile mechanism, same variable names) so the two dashboard packages
# never drift apart on how they resolve a target. This subproject is
# self-contained: it reads ONLY ha/ais/.env (see .env.template) and never
# the repo root .env / deploy.conf, nor ha/sailing-dash/.env.
#
#   env_profile_load <profile>
#
# After the call these are set and exported:
#   HA_PROFILE HA_TRANSPORT HA_SSH_HOST HA_CONTAINER HA_CONFIG_DIR
#   HA_URL HA_TOKEN HA_GW_HOST HA_GW_DATA_PORT
#
# Profile -> variable prefix: uppercase, "-" becomes "_" (stage-pi5 -> STAGE_PI5).

ENV_PROFILE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# lib/ lives in ha/ais/helpers/lib/ — .env belongs to the subproject root.
ENV_PROFILE_DIR="$(cd "${ENV_PROFILE_LIB_DIR}/../.." && pwd)"
ENV_PROFILE_FILE="${ENV_PROFILE_DIR}/.env"

_env_profile_var() {
    # Indirect read that works on bash 3.2 (macOS): no ${!name}, no ${x^^}.
    eval "printf '%s' \"\${$1:-}\""
}

env_profile_prefix() {
    printf '%s' "$1" | tr '[:lower:]-' '[:upper:]_'
}

env_profile_list() {
    if [[ -f "${ENV_PROFILE_FILE}" ]]; then
        # shellcheck source=/dev/null
        source "${ENV_PROFILE_FILE}"
    fi
    printf '%s' "${HA_PROFILES:-stage prod}"
}

env_profile_load() {
    local profile="${1:-stage}"

    if [[ -f "${ENV_PROFILE_FILE}" ]]; then
        # .env is a shell fragment written as VAR="${VAR:-default}", so a real
        # environment variable still overrides it (PROD_CONTAINER=ha-test ./deploy.sh …).
        # shellcheck source=/dev/null
        source "${ENV_PROFILE_FILE}"
    elif [[ "${profile}" != "stage" && "${profile}" != "prod" ]]; then
        echo "ERROR: profile '${profile}' requires ${ENV_PROFILE_FILE}." >&2
        echo "       Create it:  cp ${ENV_PROFILE_DIR}/.env.template ${ENV_PROFILE_DIR}/.env" >&2
        return 1
    fi

    local known="${HA_PROFILES:-stage prod}"
    case " ${known} " in
        *" ${profile} "*) ;;
        *)
            echo "ERROR: unknown target profile '${profile}'. Known profiles: ${known}" >&2
            echo "       Declare it in HA_PROFILES inside ${ENV_PROFILE_FILE}." >&2
            return 1
            ;;
    esac

    local p
    p="$(env_profile_prefix "${profile}")"

    HA_PROFILE="${profile}"
    HA_TRANSPORT="$(_env_profile_var "${p}_TRANSPORT")"
    HA_SSH_HOST="$(_env_profile_var "${p}_SSH_HOST")"
    HA_CONTAINER="$(_env_profile_var "${p}_CONTAINER")"
    HA_CONFIG_DIR="$(_env_profile_var "${p}_CONFIG_DIR")"
    HA_URL="$(_env_profile_var "${p}_HA_URL")"
    HA_TOKEN="$(_env_profile_var "${p}_HA_TOKEN")"
    HA_GW_HOST="$(_env_profile_var "${p}_GW_HOST")"
    HA_GW_DATA_PORT="$(_env_profile_var "${p}_GW_DATA_PORT")"

    # Built-in fallbacks so the two default profiles work before .env exists.
    if [[ -z "${HA_TRANSPORT}" ]]; then
        if [[ "${profile}" == "stage" ]]; then HA_TRANSPORT="local-docker"; else HA_TRANSPORT="ssh-docker"; fi
    fi
    if [[ -z "${HA_CONTAINER}" ]]; then
        if [[ "${profile}" == "stage" ]]; then HA_CONTAINER="local-ha"; else HA_CONTAINER="homeassistant"; fi
    fi
    HA_CONFIG_DIR="${HA_CONFIG_DIR:-/config}"
    HA_GW_HOST="${HA_GW_HOST:-127.0.0.1}"
    HA_GW_DATA_PORT="${HA_GW_DATA_PORT:-4001}"

    export HA_PROFILE HA_TRANSPORT HA_SSH_HOST HA_CONTAINER HA_CONFIG_DIR \
           HA_URL HA_TOKEN HA_GW_HOST HA_GW_DATA_PORT
}
