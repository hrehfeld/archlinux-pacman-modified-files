#!/usr/bin/env sh
if [[ ! -e $etc_repo/.synced ]]
then
	cd $etc_repo
	for f in $(git ls-tree -r  "!$HOST" --name-only | ag -wv branches)
	do
		[[ -e /$f ]] && cp -a /$f /$f.bak
		cp -a $f /$f
	done
fi
