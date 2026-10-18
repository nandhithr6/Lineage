# Independent held-out set: review sheet

Judge verdicts for people to check. Tick the ones you disagree with and tell the team; do not edit the questions or gold answers.

## ind-01 (ownership)
**Question:** Which individual (not an alias) is listed directly as an approver in the OWNERS file for pkg/controller/job in kubernetes/kubernetes, and who is listed there as an emeritus approver?
**Gold answer:** mimowo is the directly listed approver (alongside the sig-apps-approvers alias); alculquicondor is the emeritus approver.

- **lineage** judge: WRONG (source cited: True) - The answer lists multiple individuals as direct approvers instead of identifying mimowo as the specific individual listed alongside the alias.
  > The individuals listed directly as approvers in the `OWNERS` file for `pkg/controller/job` are: *   atiratree [e1] *   janetkuo [e2] *   kow3ns [e3] *   mimowo [e4] *   smarterclayton [e5] *   soltysh [e6]  The individual listed as an emeritus approver in that same file is: *   alculquicondor [e23]
- **rag_tfidf** judge: WRONG (source cited: False) - The answer fails to identify mimowo as the current approver for the current state of the repository and provides conflicting information across different versions.
  > For release 1.30 and 1.31, alculquicondor is listed as an approver, and there are no emeritus approvers listed [p1, p2].  For releases 1.34, 1.36, and 1.37, mimowo is listed as an approver, and alculquicondor is listed as an emeritus approver [p3, p4, p5].
- **rag_vector** judge: WRONG (source cited: False) - The answer failed to provide the required names of the approver and emeritus approver.
  > Insufficient evidence.

## ind-02 (ownership)
**Question:** Who can approve changes under pkg/volume in kubernetes/kubernetes? Resolve the OWNERS alias to people.
**Gold answer:** pkg/volume/OWNERS lists the alias sig-storage-approvers, which resolves to gnufied, jsafrane, msau42, saad-ali, thockin and xing-yang.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies all six required individuals listed under the sig-storage-approvers alias for pkg/volume.
  > The following people are listed as approvers for `pkg/volume` in `kubernetes/kubernetes`:  *   gnufied [e1] *   jsafrane [e2] *   msau42 [e3] *   saad-ali [e4] *   thockin [e5] *   xing-yang [e6]  Additionally, the following people are listed as approvers for the parent directory `pkg/`, which also grants approval authority:  *   dchen1107 [e7] *   dims [e8] *   liggitt [e9] *   smarterclayton [e10] *   thockin [e11] *   wojtek-t [e12]  (Note: brendandburns and lavalamp are listed as emeritus approvers for `pkg/` [e13][e14].)
- **rag_tfidf** judge: WRONG (source cited: False) - The answer failed to resolve the alias to the required list of six individuals as specified in the reference answer.
  > Insufficient evidence. The provided passages identify the approvers as "kubernetes:sig-storage-approvers," but they do not list the individual people associated with that alias [p1, p2, p3, p4, p5].
- **rag_vector** judge: WRONG (source cited: False) - The answer failed to resolve the alias to the required list of six individuals as specified in the reference answer.
  > Insufficient evidence. The provided passages identify the approvers as "kubernetes:sig-storage-approvers," but they do not list the individual people associated with that alias [p1, p2, p3, p4, p5].

## ind-03 (expertise)
**Question:** Which GitHub user(s) submitted a formal pull-request review on kubernetes/kubernetes PR #131868 ('Remove superflous expansion calls if controller finished expansion')?
**Gold answer:** One formal review: hashim21223445 (state APPROVED). The PR changes files under pkg/volume/util/operationexecutor.

- **lineage** judge: WRONG (source cited: False) - The answer failed to provide the required information which is available in the reference answer.
  > Insufficient evidence. The available tools do not contain information regarding pull-request reviews for kubernetes/kubernetes PR #131868.
- **rag_tfidf** judge: WRONG (source cited: False) - The answer failed to identify the reviewer hashim21223445, which is present in the PR data.
  > Insufficient evidence.
- **rag_vector** judge: WRONG (source cited: False) - The answer failed to identify the reviewer hashim21223445, which is present in the PR data.
  > Insufficient evidence.

## ind-04 (expertise)
**Question:** Which GitHub users submitted formal pull-request reviews on kubernetes/kubernetes PR #131820 ('Drop dependency on x/exp package in mount_linux_test.go')?
**Gold answer:** None. The PR (by dims, touching staging/src/k8s.io/mount-utils) has no formal GitHub reviews.

- **lineage** judge: WRONG (source cited: False) - The answer failed to state that there were no formal reviews, instead claiming insufficient evidence.
  > Insufficient evidence. The available tools do not contain information regarding specific pull-request reviews for PR #131820 in the `kubernetes/kubernetes` repository.
- **rag_tfidf** judge: WRONG (source cited: False) - The answer failed to state that there were no formal reviews for the specified PR.
  > Insufficient evidence.
- **rag_vector** judge: WRONG (source cited: False) - The answer failed to state that there were no formal reviews for the specified PR.
  > Insufficient evidence.

## ind-05 (time_travel)
**Question:** What stage was the SidecarContainers feature gate in during Kubernetes release 1.30?
**Gold answer:** Beta (beta from 1.29 to 1.32; stable and locked from 1.33).

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies that the SidecarContainers feature gate was in the beta stage during Kubernetes 1.30.
  > The SidecarContainers feature gate (an on/off switch with a maturity stage) was in the beta stage during Kubernetes release 1.30 [e3].
- **rag_tfidf** judge: WRONG (source cited: False) - The answer failed to identify that the feature gate was in Beta during Kubernetes 1.30.
  > Insufficient evidence.
- **rag_vector** judge: CORRECT (source cited: False) - The answer correctly identifies that the SidecarContainers feature gate was in the beta stage during Kubernetes 1.30.
  > During Kubernetes release 1.30, the SidecarContainers feature gate was in the beta stage [p1].

## ind-06 (time_travel)
**Question:** What stage was the CronJobTimeZone feature gate in during Kubernetes release 1.25?
**Gold answer:** Beta (alpha 1.24, beta 1.25 to 1.26, stable 1.27 to 1.28, then removed).

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies that the CronJobTimeZone feature gate was in the beta stage during Kubernetes 1.25.
  > The CronJobTimeZone feature gate (an on/off switch with a maturity stage) was in the beta stage during Kubernetes release 1.25 [e3].
- **rag_tfidf** judge: CORRECT (source cited: False) - The answer correctly identifies that the CronJobTimeZone feature gate was in the beta stage during Kubernetes 1.25.
  > During Kubernetes release 1.25, the CronJobTimeZone feature gate was in the beta stage [p1].
- **rag_vector** judge: CORRECT (source cited: False) - The answer correctly identifies that the CronJobTimeZone feature gate was in beta during Kubernetes 1.25.
  > The CronJobTimeZone feature gate was in beta during Kubernetes release 1.25 [p1].

## ind-07 (time_travel)
**Question:** What stage was the ReadWriteOncePod feature gate in during Kubernetes release 1.27?
**Gold answer:** Beta (alpha 1.22 to 1.26, beta 1.27 to 1.28, stable 1.29 to 1.30, then removed).

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies that the ReadWriteOncePod feature gate was in the beta stage during Kubernetes release 1.27.
  > The ReadWriteOncePod feature gate (an on/off switch with a maturity stage) was in the beta stage during Kubernetes release 1.27 [e3].
- **rag_tfidf** judge: CORRECT (source cited: False) - The answer correctly identifies that the ReadWriteOncePod feature gate was in beta during Kubernetes 1.27.
  > During Kubernetes release 1.27, the ReadWriteOncePod feature gate was in beta [p1].
- **rag_vector** judge: CORRECT (source cited: False) - The answer correctly identifies that the ReadWriteOncePod feature gate was in the beta stage during Kubernetes 1.27.
  > During Kubernetes release 1.27, the ReadWriteOncePod feature gate was in the beta stage [p1, p2].

## ind-08 (decision_lineage)
**Question:** Which KEP introduced the SidecarContainers feature gate, which SIG owns it, and who are its listed approvers?
**Gold answer:** KEP-753 'Sidecar Containers', owned by sig-node; approvers mrunalp and thockin.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies KEP-753, SIG-Node, and both required approvers.
  > The `SidecarContainers` feature gate was introduced by KEP-753, titled "Sidecar Containers" [e1]. This KEP is owned by SIG-Node [e2]. The listed approvers for this KEP are mrunalp and thockin [e7][e8].
- **rag_tfidf** judge: CORRECT (source cited: False) - The answer correctly identifies KEP-753, sig-node, and both required approvers.
  > The SidecarContainers feature gate was introduced by the KEP "Sidecar Containers" (sig-node/753) [p2]. It is owned by sig-node, and its listed approvers are mrunalp and thockin [p2].
- **rag_vector** judge: WRONG (source cited: False) - The answer failed to provide the required KEP number, SIG, and approvers.
  > Insufficient evidence.

## ind-09 (decision_lineage)
**Question:** Which related KEP does KEP-753 (Sidecar Containers) list under see-also?
**Gold answer:** keps/sig-node/4438-sidecar-restart-termination (KEP-4438).

- **lineage** judge: WRONG (source cited: True) - The answer failed to identify KEP-4438, which is explicitly listed in the KEP-753 documentation.
  > Insufficient evidence. The KEP-753 (Sidecar Containers) design document does not list any related KEPs under a "see-also" section in the available data [e1].
- **rag_tfidf** judge: WRONG (source cited: False) - The answer failed to identify KEP-4438, which is explicitly listed in the reference answer.
  > Insufficient evidence.
- **rag_vector** judge: WRONG (source cited: False) - The answer failed to identify KEP-4438, which is explicitly listed in the reference answer.
  > Insufficient evidence.

## ind-10 (docs_drift)
**Question:** The Persistent Volumes docs page labels the ReadWriteOncePod access mode 'stable since v1.29'. Is that label consistent with the feature gate's history?
**Gold answer:** Yes, no drift. The ReadWriteOncePod gate became stable in 1.29 (stable 1.29 to 1.30, then removed after graduating), so 'stable since v1.29' is correct.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly confirms the consistency between the documentation and the feature gate's history, noting the stable release in v1.29.
  > Yes, the label in the Persistent Volumes documentation is consistent with the feature gate's history. The `ReadWriteOncePod` feature gate reached the stable stage in Kubernetes v1.29 [e16].  Evidence: *   The Persistent Volumes documentation claims `ReadWriteOncePod` has been stable since v1.29 [e6]. *   The feature gate history for `ReadWriteOncePod` confirms it was in the stable stage starting in v1.29 [e16].
- **rag_tfidf** judge: CORRECT (source cited: False) - The answer correctly confirms the consistency between the documentation label and the feature gate's graduation to stable in version 1.29.
  > Yes, the label is consistent with the feature gate's history. The `ReadWriteOncePod` feature gate reached stable status in version 1.29 [p3], and the "Access Modes" section of the "Persistent Volumes" documentation labels the access mode as stable since v1.29 [p1].
- **rag_vector** judge: CORRECT (source cited: False) - The answer correctly confirms the consistency between the documentation label and the feature gate's graduation to stable in version 1.29.
  > Yes, the label is consistent with the feature gate's history. The `ReadWriteOncePod` feature gate reached stable status in version 1.29 [p2], and the Persistent Volumes documentation labels the access mode as stable since v1.29 [p3].

## ind-11 (docs_drift)
**Question:** Is the feature-state label at the top of the Sidecar Containers docs page hard-coded, and can it go out of date?
**Gold answer:** No. It uses feature_gate_name="SidecarContainers", so the stage is looked up from the gate file and cannot drift. The gate is currently stable (locked) since 1.33.

- **lineage** judge: WRONG (source cited: True) - The answer incorrectly claims the label can go out of date, whereas the reference answer states it cannot drift because it is looked up from the gate file.
  > The `feature-state` label on the "Sidecar Containers" documentation page is not hard-coded; it is generated by a shortcode, `{{< feature-state feature_gate_name="SidecarContainers" >}}` [e6].  Because this label is dynamically linked to the `SidecarContainers` feature gate, it can go out of date if the documentation is not updated when the feature's maturity stage changes [e3][e4]. The `SidecarContainers` feature gate progressed from alpha (v1.28) and beta (v1.29–v1.32) to stable in v1.33 [e5][e7].
- **rag_tfidf** judge: WRONG (source cited: False) - The answer incorrectly claims insufficient evidence instead of explaining that the label uses a feature gate lookup to prevent drift.
  > Insufficient evidence. The provided passages do not state whether the feature-state label is hard-coded or if it can go out of date.
- **rag_vector** judge: WRONG (source cited: False) - The answer failed to provide the required information about the feature gate lookup mechanism and incorrectly claimed insufficient evidence.
  > Insufficient evidence.

## ind-12 (multi_hop)
**Question:** The OWNERS file for pkg/controller/job lists the alias sig-apps-approvers. Which members of that alias are also chairs of SIG Apps in sigs.yaml?
**Gold answer:** janetkuo, kow3ns and soltysh. (The alias also includes smarterclayton and atiratree, who are not SIG Apps chairs.)

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies the three required members who are SIG Apps chairs and excludes the non-chair members.
  > The members of the `sig-apps-approvers` alias listed in the `pkg/controller/job/OWNERS` file who are also chairs of SIG Apps are:  *   **janetkuo** [e2][e34] *   **kow3ns** [e3][e32] *   **soltysh** [e6][e30]
- **rag_tfidf** judge: WRONG (source cited: False) - The answer failed to provide the required list of names and instead claimed insufficient evidence.
  > Insufficient evidence.
- **rag_vector** judge: WRONG (source cited: False) - The answer failed to provide the required list of names and instead claimed insufficient evidence.
  > Insufficient evidence.

## ind-13 (multi_hop)
**Question:** Of the approvers listed on the KEP that introduced the SidecarContainers feature gate, which one is also in the sig-node-approvers alias in kubernetes/kubernetes OWNERS_ALIASES?
**Gold answer:** mrunalp. (KEP-753's approvers are mrunalp and thockin; only mrunalp is in sig-node-approvers.)

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies mrunalp as the only approver from KEP-753 who is also in the sig-node-approvers alias.
  > The KEP that introduced the `SidecarContainers` feature gate (KEP-753) lists **mrunalp** and **thockin** as approvers [e1][e7][e8].  Of these, **mrunalp** is listed as a member of the `sig-node-approvers` alias in the `kubernetes/kubernetes` `OWNERS_ALIASES` file [e19].  Evidence: *   KEP-753 approvers: `mrunalp` [e7], `thockin` [e8]. *   `sig-node-approvers` members: `dchen1107`, `derekwaynecarr`, `klueska`, `mrunalp`, `random-liu`, `sergeykanzhelev`, `sjenning`, `tallclair`, `yujuhong` [e19].
- **rag_tfidf** judge: WRONG (source cited: False) - The answer provided insufficient evidence, whereas the reference answer identifies mrunalp as the correct individual.
  > Insufficient evidence.
- **rag_vector** judge: WRONG (source cited: False) - The answer provided insufficient evidence, whereas the reference answer identifies mrunalp as the correct individual.
  > Insufficient evidence.

## ind-14 (unanswerable)
**Question:** In which Kubernetes release was the ReadWriteOncePod feature gate removed?
**Gold answer:** insufficient evidence. The gate file says removed: true and lists stable through 1.30, but it does not state the release in which the gate was removed. Guessing 1.31 is not supported by the data.

- **lineage** judge: WRONG (source cited: True) - The answer incorrectly identifies a removal release, whereas the reference answer states there is insufficient evidence.
  > The ReadWriteOncePod feature gate was removed in Kubernetes release 1.31 [e5]. It reached the stable stage in release 1.29 [e4]. [e1][e5]
- **rag_tfidf** judge: WRONG (source cited: False) - The answer incorrectly identifies a removal release when the reference answer states there is insufficient evidence.
  > The ReadWriteOncePod feature gate was removed in Kubernetes version 1.30 [p1].
- **rag_vector** judge: CORRECT (source cited: False) - The answer correctly identifies that there is insufficient evidence to determine the specific release.
  > Insufficient evidence.

## ind-15 (unanswerable)
**Question:** Which person approved the alpha milestone (v1.28) of KEP-753 specifically?
**Gold answer:** insufficient evidence. kep.yaml lists the KEP's approvers (mrunalp, thockin) but does not record who approved each individual milestone.

- **lineage** judge: WRONG (source cited: True) - The answer fails to state that there is insufficient evidence to identify the specific approver for the alpha milestone.
  > The KEP-753 ("Sidecar Containers") lists **mrunalp** and **thockin** as its approvers [e3].
- **rag_tfidf** judge: CORRECT (source cited: False) - The answer correctly identifies that there is insufficient evidence to name a specific approver for the alpha milestone.
  > Insufficient evidence. While the KEP lists mrunalp and thockin as approvers for the feature, the provided passages do not specify which individual approved the alpha milestone specifically [p1].
- **rag_vector** judge: CORRECT (source cited: False) - The answer correctly identifies that there is insufficient evidence to attribute the alpha milestone approval to a specific person.
  > Insufficient evidence.
