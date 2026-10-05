package com.example.robboss_mobile_app.domain

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

fun planSchema(): JsonObject = obj(
    required = listOf("steps"),
    properties = mapOf("steps" to arrayOf(stepSchema())),
)

fun verdictSchema(): JsonObject = obj(
    required = listOf("verdict", "category", "adjustment"),
    properties = mapOf(
        "verdict" to enumString("READY", "ADJUST"),
        "category" to enumString("value", "coverage", "stroke_direction", "none"),
        "adjustment" to stringType(),
    ),
)

private fun stepSchema(): JsonObject = obj(
    required = listOf("mask_id", "name", "mix", "brush", "technique", "stroke_dir_deg", "success"),
    properties = mapOf(
        "mask_id" to stringType(),
        "name" to stringType(),
        "mix" to arrayOf(
            obj(
                required = listOf("pigment", "parts"),
                properties = mapOf("pigment" to stringType(), "parts" to intType()),
            ),
        ),
        "brush" to stringType(),
        "technique" to stringType(),
        "stroke_dir_deg" to intType(),
        "success" to stringType(),
    ),
)

private fun obj(required: List<String>, properties: Map<String, JsonObject>) = buildJsonObject {
    put("type", "object")
    put("properties", buildJsonObject { properties.forEach { (name, schema) -> put(name, schema) } })
    put("required", buildJsonArray { required.forEach { add(it) } })
}

private fun stringType() = buildJsonObject { put("type", "string") }

private fun intType() = buildJsonObject { put("type", "integer") }

private fun enumString(vararg values: String) = buildJsonObject {
    put("type", "string")
    put("enum", buildJsonArray { values.forEach { add(it) } })
}

private fun arrayOf(items: JsonObject) = buildJsonObject {
    put("type", "array")
    put("items", items)
}
