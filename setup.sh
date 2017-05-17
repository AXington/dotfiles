#! /bin/bash


DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

git submodule update --init --recursive

ln -s -f $DIR/gpakosz-tmux/.tmux.conf $HOME/.tmux.conf
ln -s -f $DIR/tmux.conf.local $HOME/.tmux.conf.local
ln -s -f $DIR/vim $HOME/.vim
ln -s -f $DIR/vimrc $HOME/.vimrc
ln -s -f $DIR/zshrc $HOME/.zshrc

nocaps="setxkbmap -option ctrl:nocaps\n
xcape -e 'Caps_Lock=Escape;Control_L=Escape;Control_R=Escape'"

echo "Is current env linux?"
select yn in "Yes" "No"; do
	case $yn in
		Yes ) echo "$nocapS" >> $HOME/.local_settings; break;;
		No ) echo "not disabling capslock"; break;;
	esac
done

[ -z '$GIT_EMAIL' ] || git config --global user.email "$GIT_EMAIL"
[ -z '$GIT_NAME' ] || git config --global user.name "$GIT_NAME"
[ -z '$EDITOR' ] || git config --global core.editor "$EDITOR"



