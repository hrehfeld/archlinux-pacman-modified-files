#!/usr/bin/env sh
etc_repo=/home/hrehfeld/etc-repo
set -e
if [[ ! -e $etc_repo/.synced ]]
then
	cd $etc_repo
	for f in $(git ls-tree -r "!$(hostname)" --name-only --full-name | ag -wv branches)
	do
		[[ -e /$f ]] && cp -a /$f /$f.bak
		cp -a $f /$f
	done
fi
