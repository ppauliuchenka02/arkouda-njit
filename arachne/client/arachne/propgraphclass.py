"""Contains the graph class defintion for `PropGraph`."""

from __future__ import annotations
from typing import List

import arachne as ar
import arkouda as ak

__all__ = ["PropGraph"]

class PropGraph(ar.DiGraph):
    """`PropGraph` is the base class to represent a property graph. It inherits from `DiGraph` since
    all property graphs are composed of directed edges. Property graphs contain vertices (nodes) and
    edges as a graph typically does, however, their nodes and edges may contain extra information 
    referred to as attributes. Now, we will discuss each possible type of attribute in more detail.

    Nodes of a property graph contain an attribute called a `label` that may contain any number of
    extra identifiers for that node, including none. Some examples could be identifying a vertex in
    a transaction network as a `Person`, `Buyer`, and `Loyalty Member`. 

    Edges of a property graph each must contain one attribute called a `relationship`. If there are
    two instances of an edge, as in a multigraph, each edge must be uniquely identified by its
    relationship. An edge without a uniquely identifiable relationship is removed from the 
    `PropGraph` during construction. The user can specify the relationship for each edge typically
    as a column composed of a `pdarray` of strings. If the user does not specify the relationship
    while using any of the methods that loads attributes, such as `PropGraph.load_edge_attributes`
    then we remove all duplicate edges and assign every edge the same unique identifier. Continuing
    the example of the transaction network above, say we have two edges from the same buyer to an
    item. One of these edges may be identifiable as a purchase with the relationship `Buys` or as
    a return with the relationship `Returns`. This allows for multiple different interactions to be
    logged between the same two pairs of nodes. **Currently, multiple edges are not allowed but it
    is planned for a future release of Arachne.**

    Both nodes and edges can contain more properties to hold extra data for each node or edge. For
    example, a `Person` node can contain properties such as `Address`, `Phone Number`, and `Email`.
    Edges with relationship `Buys` could have a property identifying the `Shipping Address` whereas
    an edge with relationship `Returns` could have a property identifying `Return Date`.

    To query a `PropGraph` the user can extract a `pdarray` for each attribute by accessing either
    the `edge_attributes` or `node_attributes` from the attributes list of `PropGraph`. Then, the
    user can use all class methods for `ak.DataFrame`s and `pdarrays` to perform searches within
    the `PropGraph` object. 

    >>> import arachne as ar
    >>> G = ar.PropGraph()
    >>> G.load_edge_attributes(some_dataframe, src = "source", dst = "destination")
    >>> edges_of_G = G.edges()
    >>> bool_edges = G.edge_attributes["some_column"] == 1
    >>> edges_where_query_matches = edges_of_G[bool_edges]

    The above example can also be applied to node attributes. 

    Attributes
    ----------
    multied : int
        The graph is a multi graph (True) or not a multi graph (False).
    edge_attributes : ak.DataFrame
        Dataframe containing the edges of the graph and their attributes. 
    node_attributes : ak.DataFrame
        Dataframe containing the nodes of the graph and their attributes.
    relationship_mapper : Dict
        List of the attribute (column) names that correspond to relationships.
    label_mapper : Dict
        List of the attribute (column) names that correspond to labels.

    See Also
    --------
    Graph, DiGraph
        
    Notes
    -----
    """

    def __init__(self) -> None:
        """Initializes an empty graph instance."""
        super().__init__()
        self.multied = 0
        self.edge_attributes = ak.DataFrame()
        self.relationship_mapper = dict()
        self.node_attributes = ak.DataFrame()
        self.label_mapper = dict()

    def add_node_labels(self, labels:ak.DataFrame) -> None:
        """Populates the graph object with labels from a dataframe. Passed dataframe should follow
        the same format specified in the Parameters section below. The column containing the nodes
        should be called `nodes`. Every column that is not the `nodes` column is inferred to be a
        column containing labels. Duplicate nodes are removed.
        
        Parameters
        ----------
        labels : ak.DataFrame
            `ak.DataFrame({"nodes" : nodes, "labels1" : labels1, ..., "labelsN" : labelsN})`

        Returns
        -------
        None
        """
        cmd = "addNodeLabels"

        # 0. Do preliminary check to make sure any attribute (column) names do not already exist.
        try:
            [self.node_attributes[col] for col in labels.columns]
        except KeyError as exc:
            raise KeyError("duplicated attribute (column) name in labels") from exc

        # 1. Extract the nodes from the dataframe and drop them from the labels dataframe.
        vertex_ids = None
        try:
            vertex_ids = labels["nodes"]
        except KeyError as exc:
            raise KeyError("attribute (column) nodes does not exist in labels") from exc
        labels.drop("nodes", axis=1, inplace=True)

        # 2. Convert labels to integers and store the index to label mapping in the label_mapper.
        vertex_labels_symbol_table_ids = []
        vertex_labels_mapper_symbol_table_ids = []
        for col in labels.columns:
            if isinstance(labels[col],ak.Strings):
                gb_labels = ak.GroupBy(labels[col])
                new_label_ids = ak.arange(gb_labels.unique_keys.size)
                new_vertex_labels = gb_labels.broadcast(new_label_ids)
                self.label_mapper[col] = gb_labels.unique_keys
                self.node_attributes[col] = new_vertex_labels
            else:
                placeholder = ak.array([" "])
                self.label_mapper[col] = placeholder

            vertex_labels_symbol_table_ids.append(labels[col].name)
            vertex_labels_mapper_symbol_table_ids.append(self.label_mapper[col].name)

        # 3. Convert the vertex ids to internal vertex ids.
        vertex_map = self.nodes()
        inds = ak.in1d(vertex_ids, vertex_map) # Gets rid of vertex_ids that do not exist.
        vertex_ids = vertex_ids[inds]
        labels = labels[inds]
        vertex_ids = ak.find(vertex_ids, vertex_map) # Generated internal vertex representations.

        # 4. GroupBy to sort the vertex ids and remove duplicates.
        gb_vertex_ids = ak.GroupBy(vertex_ids)
        inds = gb_vertex_ids.permutation[gb_vertex_ids.segments]
        vertex_ids = vertex_ids[inds]
        labels = labels[inds]

        # 5. Prepare arguments to transmit to the Chapel back-end server.
        args = { "GraphName" : self.name,
                 "InputIndicesName" : vertex_ids.name,
                 "ColumnNames" : "+".join(labels.columns),
                 "LabelArrayNames" : "+".join(vertex_labels_symbol_table_ids),
                 "LabelMapperNames" : "+".join(vertex_labels_mapper_symbol_table_ids)
        }

        ak.generic_msg(cmd=cmd, args=args)

    def load_node_attributes(self,
                             node_attributes:ak.DataFrame,
                             node_column:str,
                             label_columns:List(str)|str|None = None) -> None:
        """Populates the graph object with attributes derived from the columns of a dataframe. Node
        properties are different from node labels where labels just extra identifiers for nodes.
        On the other hand, properties are key-value pairs more akin to storing the columns of a 
        dataframe. The column to be used as the node labels can be denoted by setting the 
        `label_column` parameter. A node can have multiple labels so `label_column` can be a list
        of column names.

        **Graph must already be pupulated with edges prior to calling this method**.
        
        Parameters
        ----------
        node_attributes : ak.DataFrame
            `ak.DataFrame({"nodes" : nodes, "attribute1" : attribute1, ..., 
                           "attributeN" : attributeN})`
        node_column : str
            The column denoting the values to be treated as the nodes of the graph.
        label_column : List(str) | str | None
            Name of the column(s) to be used to denote the labels of the nodes. 

        See Also
        --------
        add_node_labels, add_edge_relationships, add_edge_attributes
        """
        cmd = "addNodeProperties" # TODO: This function should be command-less and just call a
                                  # `PropGraph` method called add_node_properties().
        columns = node_attributes.columns

        ### Modify the inputted dataframe by sorting it.
        # 1. Sort the data and remove duplicates since each node can only have one instance of a
        #    property.
        node_attributes_gb = node_attributes.groupby([ node_column ])
        node_attributes = node_attributes[
                            node_attributes_gb.permutation[node_attributes_gb.segments]
                        ]

        # 2. Store the modified edge attributes into the class variable.
        self.node_attributes = node_attributes

        # 3. Extract the nodes column as a pdarray.
        nodes = self.node_attributes[node_column]

        # 2. Populate the graph object with labels if specified.
        if label_columns is not None and label_columns is str:
            self.add_node_labels(ak.DataFrame({
                    "nodes":nodes,
                    "labels":self.node_attributes[label_columns]
                })
            )
            columns.remove(label_columns)
        elif isinstance(label_columns, list):
            labels_to_add = {col: node_attributes[col] for col in label_columns}
            labels_to_add["nodes"] = nodes
            self.add_node_labels(ak.DataFrame(labels_to_add))

        ### Prepare the columns that are to be sent to the back-end to be stored per node.
        # 1. From columns remove nodes and any other columns that were handled by adding node
        #    labels.
        columns = [col for col in columns if col not in label_columns]
        columns.remove(node_column)

        # 2. Extract symbol table names of arrays to use in the back-end.
        column_ids = [node_attributes[col].name for col in columns]

        # 3. Generate internal indices for the nodes.
        vertex_map = self.nodes()
        inds = ak.in1d(nodes, vertex_map) # Gets rid of vertex_ids that do not exist.
        vertex_ids = nodes[inds]
        node_attributes = node_attributes[inds]
        vertex_ids = ak.find(vertex_ids, vertex_map) # Generated internal vertex representations.

        args = { "GraphName" : self.name,
                 "InputIndicesName" : vertex_ids.name,
                 "ColumnNames" : "+".join(columns),
                 "PropertyArrayNames" : "+".join(column_ids)
               }
        ak.generic_msg(cmd=cmd, args=args)

    def add_edge_relationships(self, relationships:ak.DataFrame) -> None:
        """Populates the graph object with edge relationships from a dataframe. Passed dataframe 
        should follow the same format specified in the Parameters section below. The columns
        containing the edges should be called `source` for the source vertex of an edge and 
        `destination` for the destination vertex of the edge. The column with the relationships
        should be called `relationships`. 
        
        Parameters
        ----------
        relationships : ak.DataFrame
            `ak.DataFrame({"src" : source, "dst" : destination, "relationship1" : relationship1,
                           ..., "relationshipN" : relationshipN})`

        Returns
        -------
        None
        """
        cmd = "addEdgeRelationships"

        # 0. Do preliminary check to make sure any attribute (column) names do not already exist.
        try:
            [self.edge_attributes[col] for col in relationships.columns]
        except KeyError as exc:
            raise KeyError("duplicated attribute (column) name in relationships") from exc

        # 1. Extract the nodes from the dataframe and drop them from the labels dataframe.
        src, dst = (None, None)
        try:
            src, dst = (relationships["src"], relationships["dst"])
        except KeyError as exc:
            raise KeyError("attribute (column) src or dst does not exist in relationship") from exc
        relationships.drop(["src", "dst"], axis=1, inplace=True)

        # 2. Convert relationships to integers and store the index to relationship mapping in
        #    the relationship_mapper.
        edge_relationships_symbol_table_ids = []
        edge_relationships_mapper_symbol_table_ids = []
        for col in relationships.columns:
            if isinstance(relationships[col],ak.Strings):
                gb_relationships = ak.GroupBy(relationships[col])
                new_relationship_ids = ak.arange(gb_relationships.unique_keys.size)
                new_edge_relationships = gb_relationships.broadcast(new_relationship_ids)
                self.relationship_mapper[col] = gb_relationships.unique_keys
                self.edge_attributes[col] = new_edge_relationships
            else:
                placeholder = ak.array([" "])
                self.relationship_mapper[col] = placeholder

            edge_relationships_symbol_table_ids.append(relationships[col].name)
            edge_relationships_mapper_symbol_table_ids.append(self.relationship_mapper[col].name)

        # 3. Convert the source and destination vertex ids to the internal vertex_ids.
        vertex_map = self.nodes()
        src_vertex_ids = ak.find(src, vertex_map)
        dst_vertex_ids = ak.find(dst, vertex_map)

        # 4. GroupBy of the src and dst vertex ids and relationships to remove any duplicates.
        gb_edges = ak.GroupBy([src_vertex_ids,dst_vertex_ids])
        inds = gb_edges.permutation[gb_edges.segments]
        src_vertex_ids = src_vertex_ids[inds]
        dst_vertex_ids = dst_vertex_ids[inds]
        relationships = relationships[inds]

        # 5. Generate internal edge indices.
        edges = self.edges()
        internal_edge_indices = ak.find([src_vertex_ids,dst_vertex_ids],[edges[0],edges[1]])

        args = {  "GraphName" : self.name,
                  "InputIndicesName" : internal_edge_indices.name, 
                  "ColumnNames" : "+".join(relationships.columns),
                  "RelationshipArrayNames" : "+".join(edge_relationships_symbol_table_ids),
                  "RelationshipMapperNames" : "+".join(edge_relationships_mapper_symbol_table_ids)
        }

        ak.generic_msg(cmd=cmd, args=args)

    def load_edge_attributes(self,
                             edge_attributes:ak.DataFrame,
                             source_column:str,
                             destination_column:str,
                             relationship_columns:List(str)|str|None = None) -> None:
        """Populates the graph object with attributes derived from the columns of a dataframe. Edge
        properties are different from edge relationships where relationships are used to tell apart
        multiple edges. On the other hand, properties are key-value pairs more akin to storing the 
        columns of a dataframe. The column to be used as the edge relationship can be denoted by 
        setting the `relationship_column` parameter.
        
        Parameters
        ----------
        edge_attributes : ak.DataFrame
            `ak.DataFrame({"src_vertex_ids" : src_vertex_ids, "dst_vertex_ids" : dst_vertex_ids,
                           "attribute1" : attribute1, ..., "attributeN" : attributeN})`
        source_column : str
            The column denoting the values to be treated as the source vertices of an edge in 
            a graph.
        destination_column : str
            The column denoting the values to be treated as the destination vertices of an edge in
            a graph.
        relationship_column : str | None
            Name of the column to be used to denote the relationships of each edge. If unset, no
            column is used as relationships and multiple edges will be deleted.

        See Also
        --------
        add_node_labels, add_edge_relationships, add_node_attributes
        """
        cmd = "addEdgeProperties" # TODO: This function should be command-less and just call a
                                  # `PropGraph` method called add_edge_properties().
        columns = edge_attributes.columns

        ### Modify the inputted dataframe by sorting it and removing duplicates.
        # 1. Sort the data and remove duplicates.
        edge_attributes_gb = edge_attributes.groupby( [ source_column, destination_column ] )
        new_rows = edge_attributes_gb.permutation[edge_attributes_gb.segments]
        edge_attributes = edge_attributes[new_rows]
        self.multied = 0 # TODO: Allow for multigraphs in Arachne.

        # 2. Store the modified edge attributes into the class variable.
        self.edge_attributes = edge_attributes

        # 3. Initialize our src and destination arrays.
        src = edge_attributes[source_column]
        dst = edge_attributes[destination_column]

        ### Build the graph and load in relationships.
        # 1. Populate the graph object with edges.
        super().add_edges_from(src, dst)

        # 2. Populate the graph object with relationships.
        if relationship_columns is not None and relationship_columns is str:
            self.add_edge_relationships(ak.DataFrame({
                    "src":src,
                    "dst":dst,
                    "relationships":self.edge_attributes[relationship_columns]
                })
            )
            columns.remove(relationship_columns)
        elif isinstance(relationship_columns, list):
            relationships_to_add = {col: edge_attributes[col] for col in relationship_columns}
            relationships_to_add["src"] = src
            relationships_to_add["dst"] = dst
            self.add_edge_relationships(ak.DataFrame(relationships_to_add))

        ### Prepare the columns that are to be sent to the back-end to be stored per-edge.
        # 1. Remove edges sine those are sent separately and any columns marked as relationships.
        columns = [col for col in columns if col not in relationship_columns]
        columns.remove(source_column)
        columns.remove(destination_column)

        # 2. Extract symbol table names of arrays to use in the back-end.
        column_ids = [edge_attributes[col].name for col in columns]

        # 3. Generate internal indices for the edges.
        edges = self.edges()
        nodes = self.nodes()
        src = ak.find([src], [nodes])
        dst = ak.find([dst], [nodes])
        internal_indices = ak.find([src,dst], [edges[0],edges[1]])

        args = { "GraphName" : self.name,
                 "ColumnNames" : "+".join(columns),
                 "PropertyArrayNames" : "+".join(column_ids),
                 "InputIndicesName" : internal_indices.name
               }
        ak.generic_msg(cmd=cmd, args=args)

    def get_node_labels(self) -> ak.Strings | ak.pdarray | -1:
        """Returns the `pdarray` or `Strings` object holding the nodel labels of the `PropGraph`
        object. If return is -1 then no node labels found.

        Returns
        -------
        `ak.Strings` | `ak.pdarray` | `int`
            The node labels of the property graph. If return is -1 then no node labels found.
        """
        labels = None
        try:
            indexer = list(self.label_mapper.keys())
            ns = ["nodes"]
            ns.extend(indexer)
            labels = self.node_attributes[ns]
        except KeyError as exc:
            raise KeyError("no label(s) found") from exc
        return labels

    def get_node_attributes(self) -> ak.DataFrame:
        """Returns the `ak.DataFrame` object holding all the node attributes of the `PropGraph`
        object.

        Returns
        -------
        `ak.DataFrame`
            The node attributes of the graph.
        """
        return self.node_attributes

    def get_edge_relationships(self) -> ak.Strings | ak.pdarray:
        """Returns the `pdarray` or `Strings` object holding the edge relationships of the 
        `PropGraph` object. If return is -1 then no edge relationships found.

        Returns
        -------
        `ak.Strings` | `ak.pdarray` | `int`
            The edge relationships of the property graph. If return is -1 then no edge relationships 
            found.
        """
        relationships = None
        try:
            indexer = list(self.relationship_mapper.keys())
            es = ["src", "dst"]
            es.extend(indexer)
            relationships = self.edge_attributes[es]
        except KeyError as exc:
            raise KeyError("no relationship(s) found") from exc
        return relationships

    def get_edge_attributes(self) -> ak.DataFrame:
        """Returns the `ak.DataFrame` object holding all the edge attributes of the `PropGraph`
        object.

        Returns
        -------
        `ak.DataFrame`
            The edge attributes of the graph.
        """
        return self.edge_attributes

    def find_paths_of_length_one( self,
                                  node_types: ak.DataFrame,
                                  edge_types: ak.DataFrame) -> (ak.pdarray, ak.pdarray):
        """Given two dataframes specifying the node and edge types to search form returns all paths
        of length one that matches the given types.

        Parameters
        ----------
        node_types : ak.DataFrame
            Dataframes specifying the node attribute names and values to search for. 
        edge_types : ak.DataFrame
            Dataframes specifying the edge attribute names and values to search for. 

        Returns
        -------
        `(ak.pdarray,ak.pdarray)`
            Edges that contain the given types.
        """
        # 1. Get the nodes and edges that contain the specified node and edge types.
        nodes = ak.intx(self.node_attributes, node_types)
        edges = ak.intx(self.edge_attributes, edge_types)

        print(f"nodes = {nodes}")
        print(f"edges = {edges}")

        # 2. Find the overlap of returned edges and returned nodes.
        src = ak.in1d(edges[0], nodes)
        dst = ak.in1d(edges[1], nodes)

        # 3. Perform a Boolean and operation to keep only the edges where nodes were also returned
        #    in a query.
        kept_edges = src & dst

        # 4. Extract the actual edges with original node names.
        src = edges[0][kept_edges]
        dst = edges[1][kept_edges]

        return (src, dst)
    