/**
 * algochains completion — Shell completion scripts
 *
 *   algochains completion bash   >> ~/.bashrc
 *   algochains completion zsh    >> ~/.zshrc
 *   algochains completion fish   > ~/.config/fish/completions/algochains.fish
 *   algochains completion powershell >> $PROFILE
 */

// All top-level commands
const COMMANDS = [
  "doctor", "auth", "daemon", "killswitch", "audit",
  "completion", "plugin", "trigger", "config", "version",
  // Tool commands (smart mode)
  "discover-tools", "get-tool-details", "execute-dynamic-tool",
  "detect-market-regime", "get-bot-health", "browse-strategy-marketplace",
  "portfolio-summary", "get-positions", "get-account", "get-orders",
  "run-backtest", "optimize-strategy", "validate-strategy", "walk-forward-test",
  "dispatch-tower-job", "onyx-ask", "onyx-search", "graphiti-search",
  "get-quote", "get-market-data", "massive-search-endpoints",
  "place-order", "cancel-order", "close-position", "flatten-position",
  "restart-bot", "deploy-strategy", "execute-intent", "approve-intent",
  "create-shadow-portfolio", "detect-arbitrage", "analyze-sentiment",
  "get-fills", "get-platform-health", "broker-health-check", "check-risk-alerts",
  "run-evolution-cycle", "get-evolution-status", "list-evolved-strategies",
  "subscribe-to-bot", "get-subscriber-portfolio", "get-marketplace-listings",
  "get-kronos-shadow-stats", "get-signal-conflict-stats",
];

const GLOBAL_FLAGS = [
  "--profile", "--dry-run", "--safe-only", "--confirm",
  "--json", "--verbose", "--help", "--version",
];

const AUTH_SERVICES = [
  "tradovate", "alpaca", "polygon", "oanda", "ibkr", "kalshi", "onyx", "openai", "anthropic"
];
const PROFILES = ["demo", "paper", "live"];
const DAEMON_SUBCOMMANDS = ["start", "stop", "status", "logs", "install", "uninstall"];
const KILLSWITCH_SUBCOMMANDS = ["on", "off", "status"];
const PLUGIN_SUBCOMMANDS = ["install", "list", "remove", "update", "info"];
const TRIGGER_SUBCOMMANDS = ["add", "list", "disable", "enable", "remove", "logs"];
const CONFIG_SUBCOMMANDS = ["init", "show", "generate", "set", "get"];

export function generateBashCompletion(): string {
  return `# AlgoChains CLI bash completion
# Install: algochains completion bash >> ~/.bashrc
# Or:      algochains completion bash > /etc/bash_completion.d/algochains

_algochains_completion() {
    local cur prev words cword
    _init_completion || return

    local commands="${COMMANDS.join(" ")}"
    local global_flags="${GLOBAL_FLAGS.join(" ")}"
    local auth_services="${AUTH_SERVICES.join(" ")}"
    local profiles="${PROFILES.join(" ")}"
    local daemon_subcmds="${DAEMON_SUBCOMMANDS.join(" ")}"
    local killswitch_subcmds="${KILLSWITCH_SUBCOMMANDS.join(" ")}"
    local plugin_subcmds="${PLUGIN_SUBCOMMANDS.join(" ")}"
    local trigger_subcmds="${TRIGGER_SUBCOMMANDS.join(" ")}"
    local config_subcmds="${CONFIG_SUBCOMMANDS.join(" ")}"

    case "$prev" in
        algochains)
            COMPREPLY=($(compgen -W "$commands" -- "$cur"))
            return 0 ;;
        --profile)
            COMPREPLY=($(compgen -W "$profiles" -- "$cur"))
            return 0 ;;
        auth)
            COMPREPLY=($(compgen -W "set list rotate clear test" -- "$cur"))
            return 0 ;;
        set|rotate|clear|test)
            # If previous command context was auth-related
            COMPREPLY=($(compgen -W "$auth_services" -- "$cur"))
            return 0 ;;
        daemon)
            COMPREPLY=($(compgen -W "$daemon_subcmds" -- "$cur"))
            return 0 ;;
        killswitch)
            COMPREPLY=($(compgen -W "$killswitch_subcmds" -- "$cur"))
            return 0 ;;
        plugin)
            COMPREPLY=($(compgen -W "$plugin_subcmds" -- "$cur"))
            return 0 ;;
        trigger)
            COMPREPLY=($(compgen -W "$trigger_subcmds" -- "$cur"))
            return 0 ;;
        config)
            COMPREPLY=($(compgen -W "$config_subcmds" -- "$cur"))
            return 0 ;;
        --broker)
            COMPREPLY=($(compgen -W "tradovate alpaca oanda ibkr" -- "$cur"))
            return 0 ;;
        --side)
            COMPREPLY=($(compgen -W "buy sell" -- "$cur"))
            return 0 ;;
    esac

    # Complete flags
    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "$global_flags" -- "$cur"))
        return 0
    fi

    COMPREPLY=($(compgen -W "$commands $global_flags" -- "$cur"))
}

complete -F _algochains_completion algochains
`;
}

export function generateZshCompletion(): string {
  return `#compdef algochains
# AlgoChains CLI zsh completion
# Install: algochains completion zsh >> ~/.zshrc
# Or:      algochains completion zsh > "\${fpath[1]}/_algochains"

_algochains() {
    local state

    _arguments \\
        '(-h --help)'{-h,--help}'[show help]' \\
        '(-v --version)'{-v,--version}'[show version]' \\
        '--profile[active profile]:profile:(demo paper live)' \\
        '--dry-run[preview without executing]' \\
        '--safe-only[block all T2/T3 tools]' \\
        '--confirm[required for T3/live tools]' \\
        '--json[structured JSON output]' \\
        '--verbose[verbose output]' \\
        '1:command:->command' \\
        '*:args:->args'

    case "$state" in
        command)
            local commands=(
                'doctor:pre-flight health checks'
                'auth:credential management'
                'daemon:background daemon control'
                'killswitch:emergency stop for all trades'
                'audit:view audit log'
                'completion:generate shell completion'
                'plugin:plugin management'
                'trigger:automation triggers'
                'config:configuration management'
                'version:show version'
                ${COMMANDS.filter(c => c.includes("-")).map(c =>
                  `'${c}:MCP tool ${c}'`
                ).join("\n                ")}
            )
            _describe 'command' commands
            ;;
        args)
            case "\${words[2]}" in
                auth)
                    case "\${words[3]}" in
                        set|rotate|clear|test)
                            local services=(${AUTH_SERVICES.map(s => `'${s}'`).join(" ")})
                            _describe 'service' services ;;
                        *)
                            local subcmds=('set' 'list' 'rotate' 'clear' 'test')
                            _describe 'subcommand' subcmds ;;
                    esac ;;
                daemon)
                    local subcmds=(${DAEMON_SUBCOMMANDS.map(s => `'${s}'`).join(" ")})
                    _describe 'subcommand' subcmds ;;
                killswitch)
                    local subcmds=(${KILLSWITCH_SUBCOMMANDS.map(s => `'${s}'`).join(" ")})
                    _describe 'subcommand' subcmds ;;
                plugin)
                    local subcmds=(${PLUGIN_SUBCOMMANDS.map(s => `'${s}'`).join(" ")})
                    _describe 'subcommand' subcmds ;;
                trigger)
                    local subcmds=(${TRIGGER_SUBCOMMANDS.map(s => `'${s}'`).join(" ")})
                    _describe 'subcommand' subcmds ;;
            esac ;;
    esac
}

_algochains "$@"
`;
}

export function generateFishCompletion(): string {
  const cmdCompletions = COMMANDS.map(c =>
    `complete -c algochains -f -n "__fish_use_subcommand" -a "${c}"`
  ).join("\n");

  return `# AlgoChains CLI fish completion
# Install: algochains completion fish > ~/.config/fish/completions/algochains.fish

# Disable file completions by default
complete -c algochains -f

# Global flags
complete -c algochains -l profile -r -d "active profile" -a "demo paper live"
complete -c algochains -l dry-run -d "preview without executing"
complete -c algochains -l safe-only -d "block all T2/T3 tools"
complete -c algochains -l confirm -d "required for T3/live tools"
complete -c algochains -l json -d "structured JSON output"
complete -c algochains -l verbose -d "verbose output"
complete -c algochains -l help -d "show help"
complete -c algochains -l version -d "show version"

# Top-level commands
${cmdCompletions}
complete -c algochains -f -n "__fish_use_subcommand" -a "doctor"     -d "pre-flight health checks"
complete -c algochains -f -n "__fish_use_subcommand" -a "auth"       -d "credential management"
complete -c algochains -f -n "__fish_use_subcommand" -a "daemon"     -d "background daemon control"
complete -c algochains -f -n "__fish_use_subcommand" -a "killswitch" -d "emergency stop"
complete -c algochains -f -n "__fish_use_subcommand" -a "audit"      -d "view audit log"
complete -c algochains -f -n "__fish_use_subcommand" -a "completion" -d "shell completion scripts"
complete -c algochains -f -n "__fish_use_subcommand" -a "plugin"     -d "plugin management"
complete -c algochains -f -n "__fish_use_subcommand" -a "trigger"    -d "automation triggers"
complete -c algochains -f -n "__fish_use_subcommand" -a "config"     -d "configuration"

# auth subcommands
complete -c algochains -f -n "__fish_seen_subcommand_from auth" -a "set"    -d "store credentials"
complete -c algochains -f -n "__fish_seen_subcommand_from auth" -a "list"   -d "list authenticated services"
complete -c algochains -f -n "__fish_seen_subcommand_from auth" -a "rotate" -d "re-enter and update credentials"
complete -c algochains -f -n "__fish_seen_subcommand_from auth" -a "clear"  -d "remove credentials"
complete -c algochains -f -n "__fish_seen_subcommand_from auth" -a "test"   -d "test credentials"

# auth set/rotate/clear/test — service names
for svc in ${AUTH_SERVICES.join(" ")}
    complete -c algochains -f -n "__fish_seen_subcommand_from set rotate clear test" -a "$svc"
end

# daemon subcommands
for sub in ${DAEMON_SUBCOMMANDS.join(" ")}
    complete -c algochains -f -n "__fish_seen_subcommand_from daemon" -a "$sub"
end

# killswitch subcommands
for sub in ${KILLSWITCH_SUBCOMMANDS.join(" ")}
    complete -c algochains -f -n "__fish_seen_subcommand_from killswitch" -a "$sub"
end

# plugin subcommands
for sub in ${PLUGIN_SUBCOMMANDS.join(" ")}
    complete -c algochains -f -n "__fish_seen_subcommand_from plugin" -a "$sub"
end

# trigger subcommands
for sub in ${TRIGGER_SUBCOMMANDS.join(" ")}
    complete -c algochains -f -n "__fish_seen_subcommand_from trigger" -a "$sub"
end

# Tool-specific flags
complete -c algochains -f -n "__fish_seen_subcommand_from place-order" -l broker -r -a "tradovate alpaca oanda ibkr"
complete -c algochains -f -n "__fish_seen_subcommand_from place-order" -l side -r -a "buy sell"
complete -c algochains -f -n "__fish_seen_subcommand_from place-order" -l order-type -r -a "market limit stop stop_limit"
`;
}

export function generatePowershellCompletion(): string {
  return `# AlgoChains CLI PowerShell completion
# Install: algochains completion powershell >> $PROFILE

Register-ArgumentCompleter -Native -CommandName algochains -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)

    $commands = @(
        'doctor', 'auth', 'daemon', 'killswitch', 'audit',
        'completion', 'plugin', 'trigger', 'config', 'version',
        ${COMMANDS.filter(c => c.includes("-")).map(c => `'${c}'`).join(", ")}
    )

    $globalFlags = @(
        '--profile', '--dry-run', '--safe-only', '--confirm',
        '--json', '--verbose', '--help', '--version'
    )

    $allTokens = $commandAst.CommandElements
    $prevToken = if ($allTokens.Count -gt 1) { $allTokens[$allTokens.Count - 2].ToString() } else { '' }
    $subCommand = if ($allTokens.Count -gt 1) { $allTokens[1].ToString() } else { '' }

    switch ($prevToken) {
        '--profile' {
            'demo', 'paper', 'live' | Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
            return
        }
        '--broker' {
            'tradovate', 'alpaca', 'oanda', 'ibkr' | Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
            return
        }
        '--side' {
            'buy', 'sell' | Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
            return
        }
    }

    switch ($subCommand) {
        'auth' {
            $subs = @('set', 'list', 'rotate', 'clear', 'test')
            $services = @(${AUTH_SERVICES.map(s => `'${s}'`).join(", ")})
            $completions = if ($allTokens.Count -le 2) { $subs } else { $services }
            $completions | Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
            return
        }
        'daemon' {
            @(${DAEMON_SUBCOMMANDS.map(s => `'${s}'`).join(", ")}) | Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
            return
        }
        'killswitch' {
            @(${KILLSWITCH_SUBCOMMANDS.map(s => `'${s}'`).join(", ")}) | Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
            return
        }
        'plugin' {
            @(${PLUGIN_SUBCOMMANDS.map(s => `'${s}'`).join(", ")}) | Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
            return
        }
    }

    if ($wordToComplete -like '-*') {
        $globalFlags | Where-Object { $_ -like "$wordToComplete*" } |
        ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
    } else {
        $commands | Where-Object { $_ -like "$wordToComplete*" } |
        ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
    }
}
`;
}
