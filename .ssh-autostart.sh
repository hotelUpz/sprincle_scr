#!/bin/bash

# Путь к временному файлу окружения
SSH_ENV="$HOME/.ssh/agent-env"

function start_agent {
    echo "Инициализация нового SSH-агента..."
    /usr/bin/ssh-agent | sed 's/^echo/#echo/' > "${SSH_ENV}"
    chmod 600 "${SSH_ENV}"
    . "${SSH_ENV}" > /dev/null
    ssh-add "ssh_key.txt"
}

# Если файл окружения есть, пробуем подключиться
if [ -f "${SSH_ENV}" ]; then
    . "${SSH_ENV}" > /dev/null
    # Проверяем, реально ли процесс еще запущен
    ps -ef | grep ${SSH_AGENT_PID} | grep ssh-agent$ > /dev/null || {
        start_agent;
    }
else
    start_agent;
fi