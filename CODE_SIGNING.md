# Code signing policy

## Current status

**Prepared policy; signing is not enabled.** Mein Recherchearchiv currently prepares an unsigned Windows 11 x64 installer. No application acceptance, SignPath subscription or Foundation certificate has been granted or is claimed by this document.

The project intends to apply for the free open-source service from [SignPath.io](https://signpath.io/) with a certificate provided by [SignPath Foundation](https://signpath.org/), subject to the Foundation's [conditions](https://signpath.org/terms). Only after acceptance and a verified integration may release pages state that signing is provided by those services. If approved under the Foundation model, the certificate's publisher would be **SignPath Foundation**, not Diaphotizo.

## People and responsibilities

| Role | Responsible person |
| --- | --- |
| Maintainer and author with repository write authority | Detlef Koch / Diaphotizo |
| Reviewer of contributions from other people | Detlef Koch |
| Proposed release/signing approver | Detlef Koch |

This is a small, initially single-maintainer project. These roles do not imply independent two-person review. Additional authorized people must be listed here before receiving signing or repository write access. Public project identity: [Diaphotizo](https://diaphotizo.com/impressum/).

## Intended release controls

Before signing is enabled, every authorized account must have multi-factor authentication enabled for GitHub and SignPath. This is a prerequisite still to be checked during onboarding, **not an assertion that account setup has already happened**.

An approver must inspect the selected source revision, build/test result, dependency changes and resulting package before manually approving each signing request. A successful workflow does not itself authorize publication or signing. Contributions from non-maintainers require review before merging. Build scripts and workflow definitions receive the same review as application changes.

The intended signing scope is the project's own installer and its generated uninstaller. Bundled upstream Python and library binaries retain their upstream provenance; the project does not propose independently signing those third-party binaries with its project authorization. The integration must preserve the link from reviewed repository source to build artifact and account for both the inner uninstaller and outer setup executable. See the [integration preparation](installer/signpath/README.md).

Product name and version must be consistent with the release. Signing credentials must never be committed, made available to untrusted pull-request jobs or placed in the distributed installer. The unsigned build works without signing secrets. An accepted signing configuration will require a separate review before activation.

## Privacy and limitations

The application processes archive searches and annotations locally. Source downloads occur when requested by the user/operator; providers receive normal request information. Details, local browser storage and third-party policies are in [Privacy policy](PRIVACY.md).

A valid future signature would establish the signed artifact's integrity and certificate publisher. It would not guarantee complete source coverage, error-free software or the absence of all Windows reputation warnings. Until signing is active and verified, releases must remain clearly labelled **unsigned**.
