#!/usr/bin/env bash

trim_dotenv_whitespace() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

load_dotenv_file() {
    local env_file="${1:-}"
    local line=""
    local key=""
    local value=""
    local first_char=""
    local last_char=""

    [[ -n "$env_file" && -f "$env_file" ]] || return 0

    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        line="$(trim_dotenv_whitespace "$line")"

        [[ -n "$line" ]] || continue
        [[ "${line:0:1}" != "#" ]] || continue

        if [[ "$line" == export[[:space:]]* ]]; then
            line="${line#export}"
            line="$(trim_dotenv_whitespace "$line")"
        fi

        [[ "$line" == *=* ]] || continue

        key="${line%%=*}"
        value="${line#*=}"
        key="$(trim_dotenv_whitespace "$key")"
        value="$(trim_dotenv_whitespace "$value")"

        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

        if [[ ${#value} -ge 2 ]]; then
            first_char="${value:0:1}"
            last_char="${value: -1}"
            if [[ "$first_char" == "$last_char" && ( "$first_char" == "\"" || "$first_char" == "'" ) ]]; then
                value="${value:1:${#value}-2}"
            fi
        fi

        printf -v "$key" '%s' "$value"
        export "$key"
    done < "$env_file"
}
