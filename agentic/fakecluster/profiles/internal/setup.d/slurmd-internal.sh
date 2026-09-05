#!/usr/bin/env bash
# `internal` overlay, compute nodes: the login nodes' internal names resolve here too (realism; the client never sees them).
set -u
for n in login01 login02; do
  pip="$(getent hosts "$n.hpcb.test" 2>/dev/null | awk '{print $1}' | head -1)"
  [ -n "$pip" ] && ! grep -q "$n.int.hpcb.test" /etc/hosts && echo "$pip $n.int.hpcb.test" >> /etc/hosts
done
true
