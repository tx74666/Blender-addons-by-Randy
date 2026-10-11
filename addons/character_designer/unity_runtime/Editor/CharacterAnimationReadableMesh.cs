using System;
using Unity.Collections;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

namespace CharacterDesigner.Unity.Editor
{
    // Editor-only ownership boundary. Never changes the source Mesh or its importer.
    internal static class CharacterAnimationReadableMesh
    {
        internal static Mesh Copy(Mesh source)
        {
            if (source == null) throw new ArgumentNullException(nameof(source));
            Mesh copy = null;
            try
            {
                using var snapshot = MeshUtility.AcquireReadOnlyMeshData(source);
                var input = snapshot[0];
                CharacterAnimationTransfer.Require(input.vertexCount == source.vertexCount && input.vertexCount > 0 && input.subMeshCount > 0,
                    source.name + ": no complete vertex/submesh snapshot is available.");
                var writable = Mesh.AllocateWritableMeshData(1);
                bool ownsWritable = true;
                try
                {
                    var output = writable[0];
                    output.SetVertexBufferParams(input.vertexCount, source.GetVertexAttributes());
                    CharacterAnimationTransfer.Require(output.vertexBufferCount == input.vertexBufferCount,
                        source.name + ": vertex stream layout cannot be preserved.");
                    for (int stream = 0; stream < input.vertexBufferCount; ++stream)
                    {
                        var bytes = input.GetVertexData<byte>(stream);
                        var destination = output.GetVertexData<byte>(stream);
                        CharacterAnimationTransfer.Require(bytes.Length == destination.Length &&
                            input.GetVertexBufferStride(stream) == output.GetVertexBufferStride(stream),
                            source.name + ": vertex stream format cannot be preserved.");
                        destination.CopyFrom(bytes);
                    }
                    var indices = input.GetIndexData<byte>();
                    int indexSize = input.indexFormat == IndexFormat.UInt16 ? 2 : 4;
                    CharacterAnimationTransfer.Require(indices.Length % indexSize == 0, source.name + ": incomplete index buffer.");
                    output.SetIndexBufferParams(indices.Length / indexSize, input.indexFormat);
                    output.GetIndexData<byte>().CopyFrom(indices);
                    output.subMeshCount = input.subMeshCount;
                    output.lodCount = input.lodCount;
                    // LOD0 counts as one level. Selection is active only above one;
                    // an inactive curve can have the default zero slope.
                    if (input.lodCount > 1) output.lodSelectionCurve = input.lodSelectionCurve;
                    for (int sub = 0; sub < input.subMeshCount; ++sub)
                    {
                        // Raw indices still contain the original offsets. Keep baseVertex exactly once.
                        output.SetSubMesh(sub, input.GetSubMesh(sub), MeshUpdateFlags.DontRecalculateBounds);
                        // SetSubMesh preserves the only LOD0 when selection is inactive.
                        // Unity refuses SetLod in that case.
                        if (input.lodCount > 1)
                            for (int level = 0; level < input.lodCount; ++level)
                                output.SetLod(sub, level, input.GetLod(sub, level), MeshUpdateFlags.DontRecalculateBounds);
                    }
                    copy = new Mesh { name = source.name + " Character Preview", hideFlags = HideFlags.HideAndDontSave };
                    Mesh.ApplyAndDisposeWritableMeshData(writable, copy, MeshUpdateFlags.DontRecalculateBounds);
                    ownsWritable = false;
                }
                finally { if (ownsWritable) writable.Dispose(); }

                // These Allocator.None arrays are borrowed views, not owned allocations.
                // Unity Editor permits inspection outside the game/render loop; any unavailable
                // skin or shape data is a refusal, never a truncated silhouette/weight fallback.
                var bindposes = source.GetBindposes();
                copy.bindposes = bindposes.ToArray();
                var bonesPerVertex = source.GetBonesPerVertex();
                var weights = source.GetAllBoneWeights();
                long totalWeights = 0;
                foreach (byte count in bonesPerVertex) totalWeights += count;
                CharacterAnimationTransfer.Require((bonesPerVertex.Length == 0 || bonesPerVertex.Length == input.vertexCount) && totalWeights == weights.Length,
                    source.name + ": the complete variable-influence skin data is unavailable.");
                if (bonesPerVertex.Length != 0) copy.SetBoneWeights(bonesPerVertex, weights);
                if (source.blendShapeCount != 0)
                {
                    var deltaVertices = new Vector3[input.vertexCount];
                    var deltaNormals = new Vector3[input.vertexCount];
                    var deltaTangents = new Vector3[input.vertexCount];
                    for (int shape = 0; shape < source.blendShapeCount; ++shape)
                    {
                        string name = source.GetBlendShapeName(shape);
                        int frames = source.GetBlendShapeFrameCount(shape);
                        CharacterAnimationTransfer.Require(frames > 0, source.name + ": incomplete blend shape " + name + ".");
                        for (int frame = 0; frame < frames; ++frame)
                        {
                            Array.Clear(deltaVertices, 0, deltaVertices.Length);
                            Array.Clear(deltaNormals, 0, deltaNormals.Length);
                            Array.Clear(deltaTangents, 0, deltaTangents.Length);
                            source.GetBlendShapeFrameVertices(shape, frame, deltaVertices, deltaNormals, deltaTangents);
                            copy.AddBlendShapeFrame(name, source.GetBlendShapeFrameWeight(shape, frame), deltaVertices, deltaNormals, deltaTangents);
                        }
                    }
                }
                copy.bounds = source.bounds;
                CharacterAnimationTransfer.Require(copy.isReadable && copy.vertexCount == source.vertexCount &&
                    copy.subMeshCount == source.subMeshCount && copy.blendShapeCount == source.blendShapeCount,
                    source.name + ": the complete readable preview copy could not be built.");
                return copy;
            }
            catch (Exception error)
            {
                if (copy != null) Object.DestroyImmediate(copy);
                throw new InvalidOperationException(source.name + ": the Editor cannot preserve this mesh's complete geometry, skin and blend shapes for preview. " + error.Message, error);
            }
        }
    }
}
