import json
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.lines import Line2D

HERE = Path(__file__).resolve().parent


FILES = {
    "Week1_B": "knowledge_graph_relationships.json",
    "Week1_A": "week1_a_graph.json",
    "Week3_A": "week3_a_graph.json",
}

OUTPUT_JSON = HERE / "merged_knowledge_graph.json"
OUTPUT_IMAGE = HERE / "merged_knowledge_graph.png"


# Load relationships from all three documents
merged_relationships = []
seen = set()
concept_documents = {}

for document, filename in FILES.items():
    path = HERE / filename

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    relationships = data.get("relationships", [])

    print(f"{document}: {len(relationships)} relationships loaded")

    for item in relationships:
        source = item["source"].strip()
        target = item["target"].strip()
        relation = item["relationship"].strip()
        page = item["page"]

        # Normalize names to find shared concepts
        source_key = source.casefold()
        target_key = target.casefold()

        for name, key in [(source, source_key), (target, target_key)]:
            if key not in concept_documents:
                concept_documents[key] = {"name": name, "documents": set()}
            concept_documents[key]["documents"].add(document)

        # Keep document and page information
        unique_key = (document, source_key, relation.casefold(), target_key, page)

        if unique_key in seen:
            continue

        seen.add(unique_key)

        merged_relationships.append(
            {
                "source": source,
                "relationship": relation,
                "target": target,
                "page": page,
                "document": document,
            }
        )


# Find concepts appearing in multiple documents
shared_concepts = []

for concept in concept_documents.values():
    if len(concept["documents"]) > 1:
        shared_concepts.append(
            {"concept": concept["name"], "documents": sorted(concept["documents"])}
        )


# Save merged JSON
output = {
    "documents": list(FILES.keys()),
    "relationships": merged_relationships,
    "shared_concepts": shared_concepts,
}

with open(OUTPUT_JSON, "w", encoding="utf-8") as file:
    json.dump(output, file, indent=4, ensure_ascii=False)

print(f"\nTotal relationships: {len(merged_relationships)}")
print(f"Shared concepts: {len(shared_concepts)}")

for concept in shared_concepts:
    print(f"  {concept['concept']}: {', '.join(concept['documents'])}")


# Build graph
graph = nx.MultiDiGraph()

colors = {"Week1_B": "#75AADB", "Week1_A": "#80CFA9", "Week3_A": "#F0B76E"}

for item in merged_relationships:
    source = item["source"].casefold()
    target = item["target"].casefold()

    graph.add_edge(
        source,
        target,
        relationship=item["relationship"],
        document=item["document"],
        page=item["page"],
    )


# Node colors based on document membership
node_colors = []

for node in graph.nodes():
    documents = concept_documents[node]["documents"]

    if len(documents) > 1:
        node_colors.append("#D3A0ED")
    else:
        document = next(iter(documents))
        node_colors.append(colors[document])


# Render merged graph
plt.figure(figsize=(22, 16))

positions = nx.spring_layout(graph, seed=42, k=1.5)

nx.draw_networkx_nodes(graph, positions, node_color=node_colors, node_size=1700, alpha=0.9)

nx.draw_networkx_edges(graph, positions, arrows=True, arrowsize=12, width=1, alpha=0.4)

labels = {node: concept_documents[node]["name"] for node in graph.nodes()}

nx.draw_networkx_labels(graph, positions, labels=labels, font_size=7)

legend = [
    Line2D([0], [0], marker="o", color="w", label=name, markerfacecolor=color, markersize=10)
    for name, color in colors.items()
]

legend.append(
    Line2D(
        [0],
        [0],
        marker="o",
        color="w",
        label="Shared Concept",
        markerfacecolor="#D3A0ED",
        markersize=10,
    )
)

plt.legend(handles=legend, loc="upper right")

plt.title("Merged Knowledge Graph — Three Lecture PDFs", fontsize=18)

plt.axis("off")
plt.tight_layout()

plt.savefig(OUTPUT_IMAGE, dpi=300, bbox_inches="tight")

print(f"\nMerged JSON saved to {OUTPUT_JSON.name}")
print(f"Merged graph saved to {OUTPUT_IMAGE.name}")

plt.show()
