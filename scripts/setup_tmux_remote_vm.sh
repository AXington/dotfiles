#! /usr/bin/env bash

HOST=$1
USER=$2

scp .tmux.conf.local $USER@$HOST:/home/$USER/.

ssh $USER@$HOST "git clone https://github.com/gpakosz/.tmux.git"
ssh $USER@$HOST "ln -s -f .tmux/.tmux.conf"

