# Independent held-out set: review sheet

Judge verdicts for people to check. Tick the ones you disagree with and tell the team; do not edit the questions or gold answers.

## ind-01 (ownership)
**Question:** Which individual (not an alias) is listed directly as an approver in the OWNERS file for pkg/controller/job in kubernetes/kubernetes, and who is listed there as an emeritus approver?
**Gold answer:** mimowo is the directly listed approver (alongside the sig-apps-approvers alias); alculquicondor is the emeritus approver.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies both the individual approver and the emeritus approver as required.
  > The individual listed directly as an approver in the `OWNERS` file for `pkg/controller/job` is **mimowo** [e4].  The individual listed as an emeritus approver in the `OWNERS` file for `pkg/controller/job` is **alculquicondor** [e23].  Other approvers listed for this path are associated via the `sig-apps-approvers` team [e1][e2][e3][e5][e6].

## ind-02 (ownership)
**Question:** Who can approve changes under pkg/volume in kubernetes/kubernetes? Resolve the OWNERS alias to people.
**Gold answer:** pkg/volume/OWNERS lists the alias sig-storage-approvers, which resolves to gnufied, jsafrane, msau42, saad-ali, thockin and xing-yang.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies the alias and lists all six required individuals.
  > The following people are listed as approvers for `pkg/volume` in `kubernetes/kubernetes` via the `sig-storage-approvers` alias [e1][e2][e3][e4][e5][e6]:  *   gnufied [e1] *   jsafrane [e2] *   msau42 [e3] *   saad-ali [e4] *   thockin [e5] *   xing-yang [e6]

## ind-03 (expertise)
**Question:** Which GitHub user(s) submitted a formal pull-request review on kubernetes/kubernetes PR #131868 ('Remove superflous expansion calls if controller finished expansion')?
**Gold answer:** One formal review: hashim21223445 (state APPROVED). The PR changes files under pkg/volume/util/operationexecutor.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies the user who submitted the formal review.
  > The GitHub user who submitted a formal pull-request review on kubernetes/kubernetes PR #131868 is hashim21223445 [e1][e2].

## ind-04 (expertise)
**Question:** Which GitHub users submitted formal pull-request reviews on kubernetes/kubernetes PR #131820 ('Drop dependency on x/exp package in mount_linux_test.go')?
**Gold answer:** None. The PR (by dims, touching staging/src/k8s.io/mount-utils) has no formal GitHub reviews.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly states that there are no formal pull-request reviews for the specified PR.
  > No formal pull-request reviews are recorded for kubernetes/kubernetes PR #131820 [e1].

## ind-05 (time_travel)
**Question:** What stage was the SidecarContainers feature gate in during Kubernetes release 1.30?
**Gold answer:** Beta (beta from 1.29 to 1.32; stable and locked from 1.33).

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies that the SidecarContainers feature gate was in the beta stage during Kubernetes 1.30.
  > The SidecarContainers feature gate (an on/off switch with a maturity stage) was in the beta stage during Kubernetes release 1.30 [e3].

## ind-06 (time_travel)
**Question:** What stage was the CronJobTimeZone feature gate in during Kubernetes release 1.25?
**Gold answer:** Beta (alpha 1.24, beta 1.25 to 1.26, stable 1.27 to 1.28, then removed).

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies that the CronJobTimeZone feature gate was in the beta stage during Kubernetes 1.25.
  > The CronJobTimeZone feature gate was in the beta stage during Kubernetes release 1.25 [e3].

## ind-07 (time_travel)
**Question:** What stage was the ReadWriteOncePod feature gate in during Kubernetes release 1.27?
**Gold answer:** Beta (alpha 1.22 to 1.26, beta 1.27 to 1.28, stable 1.29 to 1.30, then removed).

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies that the ReadWriteOncePod feature gate was in the beta stage during Kubernetes 1.27.
  > The ReadWriteOncePod feature gate was in the beta stage during Kubernetes release 1.27 [e3].

## ind-08 (decision_lineage)
**Question:** Which KEP introduced the SidecarContainers feature gate, which SIG owns it, and who are its listed approvers?
**Gold answer:** KEP-753 'Sidecar Containers', owned by sig-node; approvers mrunalp and thockin.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies KEP-753, SIG Node, and both required approvers.
  > The `SidecarContainers` feature gate was introduced by KEP-753, titled "Sidecar Containers" [e1]. It is owned by SIG Node [e2]. The listed approvers for this KEP are mrunalp and thockin [e7][e8].

## ind-09 (decision_lineage)
**Question:** Which related KEP does KEP-753 (Sidecar Containers) list under see-also?
**Gold answer:** keps/sig-node/4438-sidecar-restart-termination (KEP-4438).

- **lineage** judge: WRONG (source cited: True) - The answer fails to identify KEP-4438, which is explicitly listed in the KEP-753 documentation.
  > The KEP-753 (Sidecar Containers) design document does not list any related KEPs under a "see-also" section [e1][e20].

## ind-10 (docs_drift)
**Question:** The Persistent Volumes docs page labels the ReadWriteOncePod access mode 'stable since v1.29'. Is that label consistent with the feature gate's history?
**Gold answer:** Yes, no drift. The ReadWriteOncePod gate became stable in 1.29 (stable 1.29 to 1.30, then removed after graduating), so 'stable since v1.29' is correct.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly confirms the consistency of the label with the feature gate's history and accurately identifies the transition to stable in v1.29.
  > Yes, the label on the Persistent Volumes documentation page is consistent with the feature gate's history. The `ReadWriteOncePod` feature gate reached the stable stage in Kubernetes v1.29 [e16].  Evidence: *   The documentation page for Persistent Volumes contains a hard-coded label stating `ReadWriteOncePod` has been stable since v1.29 [e6]. *   The `ReadWriteOncePod` feature gate history confirms it was in the beta stage during v1.27–v1.28 and transitioned to the stable stage starting in v1.29 [e15][e16].

## ind-11 (docs_drift)
**Question:** Is the feature-state label at the top of the Sidecar Containers docs page hard-coded, and can it go out of date?
**Gold answer:** No. It uses feature_gate_name="SidecarContainers", so the stage is looked up from the gate file and cannot drift. The gate is currently stable (locked) since 1.33.

- **lineage** judge: WRONG (source cited: True) - The answer fails to mention that the gate is stable (locked) since 1.33 as required by the reference answer.
  > The feature-state label on the Sidecar Containers documentation page is not hard-coded; it names its feature gate (`SidecarContainers`), which means its stage is looked up from the gate file [e1]. Consequently, it can never be out of date [e1].

## ind-12 (multi_hop)
**Question:** The OWNERS file for pkg/controller/job lists the alias sig-apps-approvers. Which members of that alias are also chairs of SIG Apps in sigs.yaml?
**Gold answer:** janetkuo, kow3ns and soltysh. (The alias also includes smarterclayton and atiratree, who are not SIG Apps chairs.)

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies the three individuals who are both members of the alias and chairs of SIG Apps.
  > The members of the `sig-apps-approvers` alias who are also chairs of SIG Apps are:  *   **janetkuo** [e2][e34] *   **kow3ns** [e3][e32] *   **soltysh** [e6][e30]

## ind-13 (multi_hop)
**Question:** Of the approvers listed on the KEP that introduced the SidecarContainers feature gate, which one is also in the sig-node-approvers alias in kubernetes/kubernetes OWNERS_ALIASES?
**Gold answer:** mrunalp. (KEP-753's approvers are mrunalp and thockin; only mrunalp is in sig-node-approvers.)

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies both approvers and correctly determines that only mrunalp is in the sig-node-approvers alias.
  > The KEP that introduced the `SidecarContainers` feature gate (KEP-753) lists `mrunalp` and `thockin` as approvers [e1][e7][e8].  Of these, `mrunalp` is listed as a member of the `sig-node-approvers` alias in `kubernetes/kubernetes` `OWNERS_ALIASES` [e36].

## ind-14 (unanswerable)
**Question:** In which Kubernetes release was the ReadWriteOncePod feature gate removed?
**Gold answer:** insufficient evidence. The gate file says removed: true and lists stable through 1.30, but it does not state the release in which the gate was removed. Guessing 1.31 is not supported by the data.

- **lineage** judge: CORRECT (source cited: True) - The answer correctly identifies that the information is not available in the gate file.
  > The Kubernetes release in which the ReadWriteOncePod feature gate was removed is not recorded in the gate file [e1].

## ind-15 (unanswerable)
**Question:** Which person approved the alpha milestone (v1.28) of KEP-753 specifically?
**Gold answer:** insufficient evidence. kep.yaml lists the KEP's approvers (mrunalp, thockin) but does not record who approved each individual milestone.

- **lineage** judge: WRONG (source cited: False) - The answer claims the KEP does not exist rather than stating there is insufficient evidence to identify a specific milestone approver.
  > The requested KEP-753 is not found in the graph. The search results provided information on other KEPs, but none matching "753" [e1][e2][e3][e4][e5].
