// Presentation aliases only. Exercise IDs, raw values and grouping stay intact.
const labels: Record<string, string> = {
  "Barbell Bench Press": "벤치 프레스",
  "Barbell Back Squat": "스쿼트",
  "Conventional Deadlift": "데드리프트",
  "Pull-up": "풀업",
  Shoulder: "어깨",
  Shoulders: "어깨",
  Chest: "가슴",
  Back: "등",
  Quads: "대퇴사두근",
  Hamstrings: "햄스트링",
  Triceps: "삼두근",
  Biceps: "이두근",
  Glutes: "둔근",
  Abs: "복근",
  Calves: "종아리",
  Core: "코어",
  Unknown: "미상",
};
export function displayLabel(label: string): string {
  return label
    .split(" · ")
    .map((part) => labels[part] ?? part)
    .join(" · ");
}
const issues: Record<string, string> = {
  invalid_session_date: "세션 날짜 이상",
  future_session_date: "미래 수행",
  invalid_session_relation: "세션 관계 문제",
  unlinked_exercise: "종목 미연결",
  unconfirmed_condition: "조건 미확인",
  invalid_reps: "반복 수 이상",
  missing_or_invalid_load: "중량 누락·이상",
  negative_load: "음수 중량",
  unconfirmed_load_unit: "중량 단위 미확인",
  unconfirmed_pullup_mode: "풀업 방식 미확인",
  unclassified_set_type: "미분류",
  e1rm_overflow: "e1RM 수치 범위 초과",
  volume_overflow: "볼륨 수치 범위 초과",
  volume_ineligible: "볼륨 계산 제외",
  ineligible_e1rm: "e1RM 계산 제외",
  ineligible_pullup_reps: "풀업 반복 계산 제외",
  outside_range: "기간 밖 기록",
  missing_baseline: "고정 기준 없음",
};
export function issueLabel(key: string): string {
  return issues[key] ?? "계산 제외 사유";
}
