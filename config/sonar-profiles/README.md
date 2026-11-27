# SonarQube quality profile backups

Backups of SonarQube quality profiles, exported from SonarQube Community 26.9 with the sonar-cxx 2.3.0 plugin.
Rule keys, severities and parameters are unchanged from the source profiles. The only key changes are two custom rules (see below).

| File | Profile | Language | Rules | What it is |
|---|---|---|---|---|
| cpp-strict.cxx.xml | cpp-strict | cxx | 970 | Bug and security rules at defaults, two style rules (braces on if/else, `//comment` style), and cognitive complexity (`FunctionCognitiveComplexity`, CRITICAL, max=15). |
| cppcheck-perf.cxx.xml | cppcheck-perf | cxx | 16 | Custom rules `other:cppcheck_perf_<id>`. These report cppcheck "performance" findings as low-priority maintainability code smells. |
| python-default.py.xml | python-default | py | 231 | Bug and security rules only, at defaults. |
| docker-shared.docker.xml | docker-shared | docker | 28 | All Docker rules, at default severities and parameters. |
| docker-infra.docker.xml | docker-infra | docker | 18 | Docker bug and security rules only. |
| go-shared.go.xml | go-shared | go | 36 | All Go rules, at defaults. |

## Restore
Use a token that has the "Administer Quality Profiles" permission:
```
printf 'user = "%s:"\n' "$SONAR_TOKEN" | curl -s -K - -F "backup=@cpp-strict.cxx.xml" "$SONAR_HOST_URL/api/qualityprofiles/restore"
```
You can also restore from the UI: Quality Profiles > Restore. Then assign the profile to a project:
`api/qualityprofiles/add_project?project=<key>&language=<lang>&qualityProfile=<name>`.

Notes:
- **cxx needs the sonar-cxx plugin.** A sonar-cxx profile holds one language, so cpp-strict and cppcheck-perf are separate profiles.
  To use both, either restore cppcheck-perf and copy its rules into cpp-strict, or merge the two `<rules>` lists before you restore.
- **Custom rules come back from their templates.** The restore recreates `cxx:style_IfRequiresBraces` (template XPath),
  `cxx:style_LineCommentNoSpace` (template CommentRegularExpression) and `other:cppcheck_perf_*` (template other:CustomRuleTemplate).
  In the source these two style rules had project-specific key prefixes; they were renamed. If a restore reports rule failures,
  create the rules first with `api/rules/create` (templateKey, customKey, params as in the XML).
- **cppcheck-perf needs a report.** The rules only fire when the scan imports a sonar-cxx "other" report (`sonar.cxx.other.reportPaths`)
  that contains cppcheck performance findings, keyed `cppcheck_perf_<id>`.
