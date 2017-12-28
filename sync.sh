#!/usr/bin/env sh
set -e
set -x
etc_repo=/home/hrehfeld/etc-$(hostname)
backup_path=/home/hrehfeld/etc-backup
list_files() {
	#git ls-tree -r "!$(hostname)" --name-only --full-name
	hg status -nA
}
if [[ ! -e $etc_repo/.synced ]]
then
	cd $etc_repo
	for f in $(list_files  | ag -wv branches)
	do
		orgp="/$f"
		repop="$etc_repo/$f"
		backupp="$backup_path/$f"
		[[ -e $orgp ]] && mkdir -p $backupp && cp -a $orgp $backupp
		cp -a $repop $orgp
	done
fi
