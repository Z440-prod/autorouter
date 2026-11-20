_autorouter_complete() {
 local current="${COMP_WORDS[COMP_CWORD]}"
 COMPREPLY=($(compgen -W 'resume fork exec review login logout mcp plugin doctor models routing code-mode capabilities memory graph knowledge learning security --help --model --profile --sandbox --cd --image --no-alt-screen' -- "$current"))
}
complete -F _autorouter_complete autorouter
