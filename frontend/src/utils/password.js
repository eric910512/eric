/** Password policy — the same rules as backend app/core/passwords.py (the backend decides). */
export const PASSWORD_MIN = 8
export const PASSWORD_MAX = 128

/** Issues with a new password (empty = acceptable); same wording as the API's details. */
export function passwordProblems(password, current = null) {
  if (typeof password !== 'string') return ['must be a string']
  const problems = []
  if (password.length < PASSWORD_MIN || password.length > PASSWORD_MAX) problems.push(`must be ${PASSWORD_MIN}–${PASSWORD_MAX} characters`)
  if (!(/\p{L}/u.test(password) && /\d/.test(password))) problems.push('must contain both letters and digits')
  if (current !== null && password === current) problems.push('must differ from the current password')
  return problems
}
