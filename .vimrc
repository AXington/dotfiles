syntax enable
set background=dark
colorscheme CiapreBlack
inoremap jj <ESC>
let mapleader = ","
set tabstop=4
set number
set modeline
filetype plugin indent on
execute pathogen#infect('bundle/{}')
autocmd BufNewFile,BufRead *.json set ft=javascript
set term=screen-256color
