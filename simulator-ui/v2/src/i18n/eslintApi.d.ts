/**
 * 046 wave 2: the one part of the ESLint 8 API that `moneyLocaleLint.policy.test.ts` uses (ESLint 8 ships no types;
 * no `@types/eslint` dependency is added for one test). Only what the test calls is declared.
 */
declare module 'eslint' {
  export type LintMessage = { ruleId: string | null; message: string }
  export type LintResult = { messages: LintMessage[] }
  export interface ESLintInstance {
    lintText(code: string, options: { filePath: string }): Promise<LintResult[]>
  }
  export function loadESLint(options: { useFlatConfig: boolean }): Promise<new (options: { cwd: string }) => ESLintInstance>
}
