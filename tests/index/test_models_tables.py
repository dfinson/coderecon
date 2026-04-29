"""Smoke tests for index table model definitions."""

from coderecon.index.models_tables import (
    CandidateContext,
    DefSnapshotRecord,
    DocCodeEdgeFact,
    DocCrossRef,
    EndpointFact,
    Epoch,
    FileChunkVec,
    FileState,
    InterfaceImplFact,
    LexicalHit,
    LintStatusFact,
    MemberAccessFact,
    ReceiverShapeFact,
    RepoState,
    SpladeVec,
    TestCoverageFact,
    TypeAnnotationFact,
    TypeMemberFact,
)


def test_table_models_are_importable():
    table_classes = [
        TypeAnnotationFact,
        TypeMemberFact,
        MemberAccessFact,
        InterfaceImplFact,
        ReceiverShapeFact,
        RepoState,
        Epoch,
        DefSnapshotRecord,
        TestCoverageFact,
        LintStatusFact,
        EndpointFact,
        DocCrossRef,
        SpladeVec,
        FileChunkVec,
        DocCodeEdgeFact,
    ]
    for cls in table_classes:
        assert hasattr(cls, "__tablename__"), f"{cls.__name__} missing __tablename__"


def test_non_table_models():
    for cls in (FileState, CandidateContext, LexicalHit):
        assert cls is not None
