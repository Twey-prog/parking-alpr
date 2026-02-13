#!/bin/bash
# Docker firewall rules to allow container access to LAN cameras
# Add this to /etc/rc.local or systemd to run at boot

# Allow Docker default bridge (172.17.0.0/16) to access local network
iptables -C DOCKER-USER -s 172.17.0.0/16 -d 192.168.1.0/24 -j ACCEPT 2>/dev/null || \
    iptables -I DOCKER-USER -s 172.17.0.0/16 -d 192.168.1.0/24 -j ACCEPT

# Allow parking_net bridge (172.18.0.0/16) to access local network
iptables -C DOCKER-USER -s 172.18.0.0/16 -d 192.168.1.0/24 -j ACCEPT 2>/dev/null || \
    iptables -I DOCKER-USER -s 172.18.0.0/16 -d 192.168.1.0/24 -j ACCEPT

echo "Docker firewall rules configured"
