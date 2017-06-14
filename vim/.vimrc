syntax enable
let mapleader = ","
set tabstop=4
set number
set modeline
filetype plugin indent on
execute pathogen#infect('bundle/{}')
autocmd BufNewFile,BufRead *.json set ft=javascript
set term=screen-256color
set listchars=tab:>-,trail:~,extends:>,precedes:<
set list
set shiftwidth=4
autocmd FileType yaml setlocal ts=2 sts=2 sw=2 expandtab
autocmd BufNewFile,BufRead *.yaml.tmpl set ft=yaml~

" Disable Arrow keys in Escape mode
map <up> <nop>
map <down> <nop>
map <left> <nop>
map <right> <nop>

imap jj <esc>

