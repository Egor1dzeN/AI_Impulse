#!/usr/bin/env bash
# Последовательный прогон нескольких датасетов (по одному — чтобы не упираться в лимиты GigaChat) + оценка.
# Использование: bash competition_task/datasets/run_series.sh <tag> vera denis artem olga maria
# vera — это competition_task/test_data (свой раннер/оценщик), остальные — datasets/<slug>.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"
tag="$1"; shift
py=backend/.venv/Scripts/python.exe
export PYTHONIOENCODING=utf-8
summary="competition_task/iterations/scores_${tag}.txt"
: > "$summary"
for slug in "$@"; do
    if [ "$slug" = "vera" ]; then
        rm -f competition_task/test_data/eval_results.json
        $py competition_task/test_data/run_test.py > competition_task/test_data/eval_run.log 2>&1
        echo "vera: $($py competition_task/test_data/grade.py | sed -n 3p)" >> "$summary"
    else
        rm -f "competition_task/datasets/$slug/eval_results.json"
        $py competition_task/datasets/run_dataset.py "$slug" --user "eval_$slug" > "competition_task/datasets/$slug/eval_run.log" 2>&1
        $py competition_task/datasets/grade_dataset.py "$slug" >> "$summary"
    fi
    mkdir -p "competition_task/iterations/$tag"
    cp -f "competition_task/datasets/$slug/grade_report.md" "competition_task/iterations/$tag/$slug.md" 2>/dev/null \
        || cp -f competition_task/test_data/grade_report.md "competition_task/iterations/$tag/vera.md"
done
echo "SERIES DONE" >> "$summary"
