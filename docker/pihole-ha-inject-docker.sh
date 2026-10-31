#!/bin/bash
# Injects pihole-ha web UI files into Pi-hole admin panel
# Runs inside the pihole container before start.sh
# Source files appear in /pihole-ha-src/ from the sidecar container

SRCDIR="/pihole-ha-src"
WEBDIR="/var/www/html/admin"
SIDEBAR="$WEBDIR/scripts/lua/sidebar.lp"

inject_ha() {
    # Wait for source files from sidecar (it stages them on startup)
    local waited=0
    while [[ ! -f "$SRCDIR/ha.lp" ]] && (( waited < 60 )); do
        sleep 1
        (( waited++ ))
    done

    if [[ ! -f "$SRCDIR/ha.lp" ]]; then
        echo "pihole-ha-inject: source files not found after 60s — skipping"
        return
    fi

    # Wait for web directory (FTL creates it)
    waited=0
    while [[ ! -d "$WEBDIR/scripts/js" ]] && (( waited < 60 )); do
        sleep 1
        (( waited++ ))
    done

    if [[ ! -d "$WEBDIR/scripts/js" ]]; then
        echo "pihole-ha-inject: web directory not ready — skipping"
        return
    fi

    # Copy page files
    cp "$SRCDIR/ha.lp" "$WEBDIR/ha.lp"
    cp "$SRCDIR/ha-api.lp" "$WEBDIR/ha-api.lp"
    cp "$SRCDIR/ha.js" "$WEBDIR/scripts/js/ha.js"
    # Same path the bare-metal install uses, because ha.lp reads it from there.
    if [[ -f "$SRCDIR/VERSION" ]]; then
        mkdir -p /usr/local/share/pihole-ha
        cp "$SRCDIR/VERSION" /usr/local/share/pihole-ha/VERSION
    fi
    echo "pihole-ha-inject: copied ha.lp, ha-api.lp, ha.js"

    # Patch sidebar if not already done
    if [[ ! -f "$SIDEBAR" ]]; then
        echo "pihole-ha-inject: sidebar.lp not found — skipping patch"
        return
    fi

    if grep -q 'HA Cluster' "$SIDEBAR"; then
        echo "pihole-ha-inject: sidebar already patched"
        return
    fi

    # Add 'ha' to the Tools treeview so parent menu opens
    sed -i "s/'network'})/'network', 'ha'})/" "$SIDEBAR"

    # Insert HA menu entry before "Update Gravity" using sed
    sed -i '/<!-- Update Gravity -->/i\                        <!-- HA Cluster -->\n                        <li<? if scriptname == '"'"'ha'"'"' then ?> class="active"<? end ?>>\n                            <a href="<?=webhome?>ha">\n                                <i class="fa fa-fw menu-icon fa-server"><\/i> <span>HA Cluster<\/span>\n                            <\/a>\n                        <\/li>' "$SIDEBAR"

    echo "pihole-ha-inject: sidebar patched — HA Cluster menu added"
}

# A returning container must not serve persisted DHCP leases before its HA
# agent has validated the current owner's signed lease state. This executes
# inside the Pi-hole container, before start.sh/FTL, even if the sidecar is down.
_ha_nodes_conf="${PIHOLE_HA_NODES_CONF:-/etc/pihole-ha/nodes.conf}"
if [[ -r "$_ha_nodes_conf" ]]; then
    (
        # nodes.conf is the same trusted local shell configuration the HA daemon
        # sources. Keep its defaults and quoted-value handling identical.
        HA_ENABLED=true DHCP_HA=true
        . "$_ha_nodes_conf" || exit 1
        if [[ "$HA_ENABLED" == true && "$DHCP_HA" == true ]]; then
            if [[ "${FTLCONF_dhcp_active:-false}" != false ]]; then
                echo "pihole-ha-inject: refusing environment-enforced DHCP before HA election" >&2
                exit 1
            fi
            pihole-FTL --config dhcp.active false >/dev/null || exit 1
        fi
    ) || { echo "pihole-ha-inject: DHCP boot guard failed; FTL not started" >&2; exit 1; }
fi

# Run injection in background so Pi-hole starts immediately
inject_ha &

# Hand off to THIS image's entrypoint, not a hardcoded one.
#
# The stock image starts at start.sh, but a combined image starts at its own
# script -- mpgirro/pihole-unbound's /custom-entrypoint.sh launches unbound and
# then Pi-hole. Running start.sh there skips unbound entirely, and since such a
# setup points dns.upstreams at 127.0.0.1#5335, the node comes up answering
# nothing. Set PIHOLE_HA_ENTRYPOINT in the pihole container to whatever the
# image's own entrypoint is; the two fallbacks below just save you doing that
# for the images we know about.
_real_entrypoint="${PIHOLE_HA_ENTRYPOINT:-}"
if [[ -z "$_real_entrypoint" ]]; then
    if [[ -x /custom-entrypoint.sh ]]; then
        _real_entrypoint=/custom-entrypoint.sh
    else
        _real_entrypoint=start.sh
    fi
fi
echo "pihole-ha-inject: handing off to $_real_entrypoint"
exec "$_real_entrypoint" "$@"
