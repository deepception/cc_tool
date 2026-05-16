#!/usr/bin/env bash
# init-firewall.sh: Default-deny egress with an allowlist of hosts cc_tool needs.
# Runs once at container start (postStartCommand). Reads $CLOUD to extend the
# allowlist for the chosen cloud provider.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "init-firewall.sh must run as root (invoked via sudo)" >&2
    exit 1
fi

# Flush prior state on rebuild
iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X
ipset destroy allowed-hosts 2>/dev/null || true

ipset create allowed-hosts hash:net

# ── Domains every Claude Code container needs ────────────────────────────────
BASE_DOMAINS=(
    # Claude Code CLI + telemetry
    "api.anthropic.com"
    "statsig.anthropic.com"
    "statsig.com"
    "sentry.io"
    # Package registries (cc_tool MCPs: claude-flow via npx, basic-memory via uvx)
    "registry.npmjs.org"
    "pypi.org"
    "files.pythonhosted.org"
    "astral.sh"
    # GitHub (gh CLI, PR comments, github MCP, repo clones)
    "api.github.com"
    "github.com"
    "objects.githubusercontent.com"
    "raw.githubusercontent.com"
    "codeload.github.com"
    # VS Code remote
    "marketplace.visualstudio.com"
    "vscode.blob.core.windows.net"
    "update.code.visualstudio.com"
)

# ── Cloud-specific allowlist (driven by $CLOUD ARG set in Dockerfile) ────────
# ── Extra MCP-specific domains (from cc-devcontainer --mcp-domains) ──────────
if [[ -s /etc/cc-tool/mcp-allowed-domains ]]; then
    while IFS= read -r d; do
        [[ -n "$d" && ! "$d" =~ ^[[:space:]]*# ]] && BASE_DOMAINS+=("$d")
    done < /etc/cc-tool/mcp-allowed-domains
fi

case "${CLOUD:-none}" in
    aws)
        BASE_DOMAINS+=(
            "sts.amazonaws.com"
            "s3.amazonaws.com"
            "ec2.amazonaws.com"
            "iam.amazonaws.com"
            "ssm.amazonaws.com"
            "sso.amazonaws.com"
        )
        # Subdomains under *.amazonaws.com are added at runtime via dig fallback below
        ;;
    gcp)
        BASE_DOMAINS+=(
            "accounts.google.com"
            "oauth2.googleapis.com"
            "iamcredentials.googleapis.com"
            "storage.googleapis.com"
            "compute.googleapis.com"
            "cloudresourcemanager.googleapis.com"
            "container.googleapis.com"
        )
        ;;
    none|"")
        ;;
    *)
        echo "Unknown CLOUD value: ${CLOUD}" >&2
        exit 1
        ;;
esac

# ── Resolve and add each domain to the ipset ─────────────────────────────────
for domain in "${BASE_DOMAINS[@]}"; do
    ips=$(dig +short "$domain" A | grep -E '^[0-9.]+$' || true)
    if [[ -z "$ips" ]]; then
        echo "  warn: could not resolve $domain — skipping"
        continue
    fi
    while read -r ip; do
        ipset add allowed-hosts "$ip" 2>/dev/null || true
    done <<< "$ips"
done

# ── GitHub's published IP ranges (via api.github.com/meta) ───────────────────
gh_meta=$(curl -fsSL --max-time 10 https://api.github.com/meta || echo '{}')
if echo "$gh_meta" | jq -e '.web' >/dev/null 2>&1; then
    for key in web api git packages; do
        echo "$gh_meta" | jq -r ".${key}[]?" 2>/dev/null | while read -r cidr; do
            [[ -n "$cidr" ]] && ipset add allowed-hosts "$cidr" 2>/dev/null || true
        done
    done
fi

# ── Host LAN (so VS Code port-forwarding and stdio MCPs over loopback work) ──
host_ip=$(ip route | awk '/default/ {print $3; exit}')
if [[ -n "$host_ip" ]]; then
    host_cidr=$(echo "$host_ip" | awk -F. '{print $1"."$2"."$3".0/24"}')
    ipset add allowed-hosts "$host_cidr" 2>/dev/null || true
fi

# ── Apply iptables rules ─────────────────────────────────────────────────────
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# DNS to whatever resolver the container is using (read from /etc/resolv.conf)
while read -r ns; do
    iptables -A OUTPUT -p udp -d "$ns" --dport 53 -j ACCEPT
    iptables -A OUTPUT -p tcp -d "$ns" --dport 53 -j ACCEPT
done < <(awk '/^nameserver/ {print $2}' /etc/resolv.conf)

iptables -A OUTPUT -m set --match-set allowed-hosts dst -j ACCEPT
iptables -P OUTPUT DROP
iptables -P INPUT  DROP
iptables -P FORWARD DROP

entry_count=$(ipset list allowed-hosts | grep -cE '^[0-9]' || true)
echo "✓ firewall up (cloud=${CLOUD:-none}, ${entry_count} entries)"
