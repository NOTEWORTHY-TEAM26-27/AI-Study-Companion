# Knowledge graph (Sprint 2)

Concept relationships from three CSE 4316 lecture PDFs, merged into one graph. Concepts that
show up in more than one document are linked and drawn in purple.

## Files

- `knowledge_graph_relationships.json`: relationships from Week1_B.pdf.
- `week1_a_graph.json`: relationships from Week1_A.pdf.
- `week3_a_graph.json`: relationships from Week3_A.pdf.
- `merge_knowledge_graphs.py`: merges the three files and draws the graph.
- `merged_knowledge_graph.json` and `merged_knowledge_graph.png`: the merged output.

## How to run

```bash
pip install networkx matplotlib
python research/knowledge_graph/merge_knowledge_graphs.py
```

## Results

49 relationships across the three documents. "Scrum Master" is the concept shared across
documents.
