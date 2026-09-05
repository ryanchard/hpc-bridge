#!/usr/bin/env bash
# `internal` overlay, login nodes: this node's `hostname -f` becomes its INTERNAL name. Docker registers a container's
# hostname on every network it joins, so a `hostname:` override would be resolvable from the jail too — instead the
# container keeps its public hostname and its own /etc/hosts line is rewritten so the canonical name for the short
# hostname is the internal one (that is what `hostname -f` returns). Docker DNS never learns the internal name: the
# jail cannot resolve it, exactly like a site's `*.rcc.local`. Peers get it in their /etc/hosts for realism.
set -u
short="$(hostname -s)"                       # login01 / login02
internal="${short}.int.hpcb.test"
public="$(hostname -f)"                      # login01.hpcb.test (Docker's line)
ip="$(getent hosts "$public" | awk '{print $1}' | head -1)"
if [ -n "$ip" ] && ! grep -q "$internal" /etc/hosts; then
  # replace Docker's own line (`<ip> login01.hpcb.test login01`) so the internal name comes first. /etc/hosts is a
  # bind-mounted file: `sed -i` (rename) fails with "Device or resource busy" — rewrite the same inode instead.
  new_hosts="$(sed "s/^$ip[[:space:]].*/$ip $internal $public $short/" /etc/hosts)"
  printf '%s\n' "$new_hosts" > /etc/hosts
fi
# the other login node's internal name (peers resolve each other; the client does not)
for n in login01 login02; do
  [ "$n" = "$short" ] && continue
  pip="$(getent hosts "$n.hpcb.test" 2>/dev/null | awk '{print $1}' | head -1)"
  [ -n "$pip" ] && ! grep -q "$n.int.hpcb.test" /etc/hosts && echo "$pip $n.int.hpcb.test" >> /etc/hosts
done
echo "[internal] hostname -f = $(hostname -f) (public name $public still resolves for clients)"
