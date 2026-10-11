using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json;
using Newtonsoft.Json.Serialization;

namespace CharacterDesigner.Unity.Editor
{
    // Transport JSON must preserve optional nulls. Unity inline serialization cannot do so.
    // Only public DTO fields and the three optional Link-revision DTO properties are emitted;
    // computed asset accessors, serialized backing strings and Unity managed-reference data are excluded.
    public static class CharacterAnimationCompositionJson
    {
        sealed class FieldsResolver : DefaultContractResolver
        {
            protected override JsonProperty CreateProperty(MemberInfo member, MemberSerialization memberSerialization)
            {
                var property = base.CreateProperty(member, memberSerialization);
                if (member.DeclaringType == typeof(CharacterAnimationCompositionExport))
                {
                    if (member.Name == nameof(CharacterAnimationCompositionExport.isolatedEditAction))
                        property.Converter = new ExportIdentityConverter(JsonToken.String);
                    else if (member.Name == nameof(CharacterAnimationCompositionExport.sourceActionSlot))
                        property.Converter = new ExportIdentityConverter(JsonToken.Integer);
                }
                return property;
            }

            protected override List<MemberInfo> GetSerializableMembers(Type type)
            {
                var fields = type.GetFields(BindingFlags.Instance | BindingFlags.Public)
                    .Where(field => !field.IsDefined(typeof(NonSerializedAttribute), false)).Cast<MemberInfo>();
                var optional = type.GetProperties(BindingFlags.Instance | BindingFlags.Public)
                    .Where(property => property.CanRead && property.CanWrite && property.GetIndexParameters().Length == 0 &&
                        (property.Name == "composition" || property.Name == "composition_export" || property.Name == "CompanionHoldComposition") &&
                        (property.PropertyType == typeof(CharacterAnimationComposition) || property.PropertyType == typeof(CharacterAnimationCompositionExport)))
                    .Cast<MemberInfo>();
                return fields.Concat(optional).ToList();
            }
        }

        sealed class ExportIdentityConverter : JsonConverter
        {
            readonly JsonToken token;
            public ExportIdentityConverter(JsonToken token) => this.token = token;
            public override bool CanWrite => false;
            public override bool CanConvert(Type type) => type == (token == JsonToken.String ? typeof(string) : typeof(int));
            public override object ReadJson(JsonReader reader, Type type, object existing, JsonSerializer serializer)
            {
                if (reader.TokenType != token)
                    throw new JsonSerializationException("Composition export " + reader.Path + " requires a JSON " + token + ".");
                return token == JsonToken.Integer ? Convert.ToInt32(reader.Value, CultureInfo.InvariantCulture) : reader.Value;
            }
            public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer) => throw new NotSupportedException();
        }

        static readonly JsonSerializerSettings Settings = new()
        {
            ContractResolver = new FieldsResolver(), Culture = CultureInfo.InvariantCulture,
            NullValueHandling = NullValueHandling.Include, TypeNameHandling = TypeNameHandling.None,
            DateParseHandling = DateParseHandling.None, MaxDepth = 64
        };

        public static string ToJson(object value, bool prettyPrint = false) => JsonConvert.SerializeObject(value,
            prettyPrint ? Formatting.Indented : Formatting.None, Settings);
        public static T FromJson<T>(string json) => JsonConvert.DeserializeObject<T>(json, Settings);
    }
}
